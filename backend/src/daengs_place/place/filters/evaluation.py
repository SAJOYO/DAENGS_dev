"""Three-valued evaluation shared by validation and result explanations."""

from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from daengs_place.place.filters.contract import Atom, FilterState

Truth = bool | None


def all_true(values: Iterable[Truth]) -> Truth:
    values = tuple(values)
    return False if False in values else None if None in values else True


def any_true(values: Iterable[Truth]) -> Truth:
    values = tuple(values)
    return True if True in values else None if None in values else False


def evaluate_atom(atom: "Atom", kind: str, parking: Truth, exclusive: Truth) -> Truth:
    if atom.capability == "purpose.kind":
        included = kind in atom.value
        return included if atom.op == "in" else not included
    actual = parking if atom.capability == "operations.parking" else exclusive
    return None if actual is None else actual is atom.value


def evaluate_atoms(atoms: Iterable["Atom"], kind: str, parking: Truth, exclusive: Truth) -> Truth:
    return all_true(evaluate_atom(atom, kind, parking, exclusive) for atom in atoms)


def evaluate(state: "FilterState", kind: str, parking: Truth, exclusive: Truth) -> Truth:
    if kind not in state.candidate_kinds:
        return False
    branches = (
        any_true(evaluate_atoms(b.all, kind, parking, exclusive) for b in state.hard.any)
        if state.hard.any
        else True
    )
    return all_true((evaluate_atoms(state.hard.all, kind, parking, exclusive), branches))
