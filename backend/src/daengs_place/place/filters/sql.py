"""SQL from whitelisted columns and bound values only; PostgreSQL supplies 3VL."""

from dataclasses import dataclass

from daengs_place.place.filters.capabilities import BY_ID
from daengs_place.place.filters.contract import FilterState, guard_filter_state


@dataclass(frozen=True)
class FilterSQL:
    expression: str
    parameters: dict


def compile_filter_sql(state: FilterState) -> FilterSQL:
    state = guard_filter_state(state)
    parameters: dict = {}

    def atom_sql(atom) -> str:
        key = f"filter_value_{len(parameters)}"
        column = BY_ID[atom.capability].sql_column
        if atom.capability == "purpose.kind":
            parameters[key] = [value.value for value in atom.value]
            comparison = f"({column} = ANY(CAST(:{key} AS text[])))"
            return f"(NOT {comparison})" if atom.op == "not_in" else comparison
        parameters[key] = atom.value
        return f"({column} = CAST(:{key} AS boolean))"

    def conjunction(atoms) -> str:
        return "(" + " AND ".join(atom_sql(a) for a in atoms) + ")" if atoms else "TRUE"

    common = conjunction(state.hard.all)
    branches = (
        "(" + " OR ".join(conjunction(b.all) for b in state.hard.any) + ")"
        if state.hard.any
        else "TRUE"
    )
    return FilterSQL(f"({common} AND {branches})", parameters)
