from typing import Any, Dict, TYPE_CHECKING, Tuple, Type

import sqlalchemy
from sqlalchemy.sql.elements import BooleanClauseList

import ormar  # noqa: I100, I202
from ormar.exceptions import QueryDefinitionError
from ormar.queryset.actions.query_action import QueryAction

if TYPE_CHECKING:  # pragma: nocover
    from ormar import Model

FILTER_OPERATORS = {
    "exact": "__eq__",
    "iexact": "ilike",
    "contains": "like",
    "icontains": "ilike",
    "startswith": "like",
    "istartswith": "ilike",
    "endswith": "like",
    "iendswith": "ilike",
    "isnull": "is_",
    "in": "in_",
    "gt": "__gt__",
    "gte": "__ge__",
    "lt": "__lt__",
    "lte": "__le__",
}
METHODS_TO_OPERATORS = {
    "__eq__": "exact",
    "__mod__": "contains",
    "__gt__": "gt",
    "__ge__": "gte",
    "__lt__": "lt",
    "__le__": "lte",
    "iexact": "iexact",
    "contains": "contains",
    "icontains": "icontains",
    "startswith": "startswith",
    "istartswith": "istartswith",
    "endswith": "endswith",
    "iendswith": "iendswith",
    "isnull": "isnull",
    "in": "in",
}
ESCAPE_CHARACTERS = ["%", "_"]


class FilterAction(QueryAction):
    """
    Filter Actions is populated by queryset when filter() is called.

    All required params are extracted but kept raw until actual filter clause value
    is required -> then the action is converted into text() clause.

    Extracted in order to easily change table prefixes on complex relations.
    """

    def __init__(self, filter_str: str, value: Any, model_cls: Type["Model"]) -> None:
        super().__init__(query_str=filter_str, model_cls=model_cls)
        self.filter_value = value
        self._escape_characters_in_clause()

    def has_escaped_characters(self) -> bool:
        """Check if value is a string that contains characters to escape"""
        return isinstance(self.filter_value, str) and any(
            c for c in ESCAPE_CHARACTERS if c in self.filter_value
        )

    def _split_value_into_parts(self, query_str: str) -> None:
        parts = query_str.split("__")
        if parts[-1] in FILTER_OPERATORS:
            self.operator = parts[-1]
            self.field_name = parts[-2]
            self.related_parts = parts[:-2]
        else:
            self.operator = "exact"
            self.field_name = parts[-1]
            self.related_parts = parts[:-1]

    def _escape_characters_in_clause(self) -> None:
        """
        Escapes the special characters ["%", "_"] if needed.
        Adds `%` for `like` queries.

        :raises QueryDefinitionError: if contains or icontains is used with
        ormar model instance
        :return: escaped value and flag if escaping is needed
        :rtype: Tuple[Any, bool]
        """
        self.has_escaped_character = False
        if self.operator in [
            "contains",
            "icontains",
            "startswith",
            "istartswith",
            "endswith",
            "iendswith",
        ]:
            if isinstance(self.filter_value, ormar.Model):
                raise QueryDefinitionError(
                    "You cannot use contains and icontains with instance of the Model"
                )
            self.has_escaped_character = self.has_escaped_characters()
            if self.has_escaped_character:
                self._escape_chars()
            self._prefix_suffix_quote()

    def _escape_chars(self) -> None:
        """Actually replaces chars to escape in value"""
        for char in ESCAPE_CHARACTERS:
            self.filter_value = self.filter_value.replace(char, f"\\{char}")

    def _prefix_suffix_quote(self) -> None:
        """
        Adds % to the beginning of the value if operator checks for containment and not
        starts with.

        Adds % to the end of the value if operator checks for containment and not
        end with.
        :return:
        :rtype:
        """
        prefix = "%" if "start" not in self.operator else ""
        sufix = "%" if "end" not in self.operator else ""
        self.filter_value = f"{prefix}{self.filter_value}{sufix}"

    def _get_composite_pk_column(self, column_name: str) -> sqlalchemy.Column:
        """Shortcut to sqlalchemy column of filtered target model"""
        relation_field = self.target_model.Meta.model_fields[self.field_name]
        aliased_name = relation_field.names.get(column_name)
        return self.target_model.Meta.table.columns[aliased_name]

    def _prepare_composite_primary_keys_filter(
        self, op_attr: str, filter_value: Dict
    ) -> BooleanClauseList:
        if op_attr != "__eq__":
            raise QueryDefinitionError(
                "You cannot use ormar.Model in filters " "different than equals!"
            )
        clauses = [
            getattr(self._get_composite_pk_column(key), op_attr)(filter_value[key])
            for key in filter_value.keys()
        ]
        return sqlalchemy.sql.and_(*clauses)

    def get_text_clause(self) -> sqlalchemy.sql.expression.BinaryExpression:
        """
        Escapes characters if it's required.
        Substitutes values of the models if value is a ormar Model with its pk value.
        Compiles the clause.
        :return: complied and escaped clause
        :rtype: sqlalchemy.sql.elements.TextClause
        """
        if isinstance(self.filter_value, ormar.Model):
            self.filter_value = self.filter_value.pk

        op_attr = FILTER_OPERATORS[self.operator]

        if self.operator == "isnull":
            op_attr = "is_" if self.filter_value else "isnot"
            filter_value = None
        else:
            filter_value = self.filter_value

        if isinstance(filter_value, dict):
            clause = self._prepare_composite_primary_keys_filter(
                op_attr=op_attr, filter_value=filter_value
            )
        else:
            if self.table_prefix:
                aliased_table = (
                    self.source_model.Meta.alias_manager.prefixed_table_name(
                        self.table_prefix, self.column.table
                    )
                )
                aliased_column = getattr(aliased_table.c, self.column.name)
            else:
                aliased_column = self.column
            clause = getattr(aliased_column, op_attr)(filter_value)

        if self.has_escaped_character:
            clause.modifiers["escape"] = "\\"
        return clause
