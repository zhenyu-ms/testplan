# decouple represetation from evaluation
# typevar always of kind *, type hint won't work much here

from dataclasses import dataclass
from typing import Any, Callable, Generic, Set, TypeVar

from typing_extensions import Protocol, Self

T = TypeVar("T")
U = TypeVar("U")


class Functor(Protocol, Generic[T]):
    def map(self, f: Callable[[T], U]) -> Self:
        # map :: f t -> (t -> u) -> f u
        ...


@dataclass
class And2(Generic[T]):
    lterm: T
    rterm: T

    def map(self, f):
        return And2(f(self.lterm), f(self.rterm))


@dataclass
class Or2(Generic[T]):
    lterm: T
    rterm: T

    def map(self, f):
        return Or2(f(self.lterm), f(self.rterm))


@dataclass
class Not(Generic[T]):
    term: T

    def map(self, f):
        return Not(f(self.term))


@dataclass
class Eq(Generic[U]):
    val: U

    def map(self, f):
        return self


Expr = TypeVar("Expr", bound=Functor)


def cata(f: Callable, rep: Expr):
    # cata :: (f t -> t) -> f (f (f ...)) -> t
    return f(rep.map(lambda x: cata(f, x)))


def eval_on_set(s: Set) -> Callable:
    def _(x):
        if isinstance(x, Eq):
            return {i for i in s if i == x.val}
        if isinstance(x, And2):
            return x.lterm & x.rterm
        if isinstance(x, Or2):
            return x.lterm | x.rterm
        if isinstance(x, Not):
            return s - x.term
        raise TypeError(f"unexpected {x}")

    return _


def apply_on_set(rep: Expr, s: Set) -> Set:
    return cata(eval_on_set(s), rep)


def apply_single(rep: Expr, i: Any) -> bool:
    def _(x):
        if isinstance(x, Eq):
            return x.val == i
        if isinstance(x, And2):
            return x.lterm and x.rterm
        if isinstance(x, Or2):
            return x.lterm or x.rterm
        if isinstance(x, Not):
            return not x.term
        raise TypeError(f"unexpected {x}")

    return cata(_(i), rep)
