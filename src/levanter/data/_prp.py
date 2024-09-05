import typing

import jax.lax
import jax.numpy as jnp
import jax.random as jrandom
import numpy as np


# TODO: do we make this a pytree
class Permutation:
    # Pseudo-Random Permutation Code
    """A stateless pseudo-random permutation.

    This class generates a pseudo-random permutation of a given length. The permutation is generated using a PRNG
    with a fixed key. The permutation is generated by finding a random `a` and `b` such that `gcd(a, length) != 1` and
    then computing the permutation as `p(x) = (a * x + b) % length`.

    This is not a very good PRP, but it is probably good enough for our purposes.
    """
    # TODO: is it actually good enough for our purposes?

    def __init__(self, length, prng_key):
        self.length = length
        self.prng_key = prng_key
        a_key, b_key = jrandom.split(prng_key)
        self._a = jrandom.randint(a_key, (), 1, length)
        self._b = jrandom.randint(b_key, (), 0, length)

        cond = lambda a_and_key: jnp.all(jnp.gcd(a_and_key[0], length) != 1)

        def loop_body(a_and_key):
            a, key = a_and_key
            this_key, key = jrandom.split(key)
            a = jrandom.randint(this_key, (), 1, length)
            return a, key

        self._a, key = jax.lax.while_loop(cond, loop_body, (self._a, a_key))

        self._a = int(self._a)
        self._b = int(self._b)

    @typing.overload
    def __call__(self, indices: int) -> int:
        ...

    @typing.overload
    def __call__(self, indices: jnp.ndarray) -> jnp.ndarray:
        ...

    def __call__(self, indices):
        if isinstance(indices, jnp.ndarray):
            # TODO: use error_if?
            # import equinox as eqx
            if jnp.any(indices < 0) or jnp.any(indices >= self.length):
                raise IndexError(f"index {indices} is out of bounds for length {self.length}")
        elif isinstance(indices, np.ndarray):
            if np.any(indices < 0) or np.any(indices >= self.length):
                raise IndexError(f"index {indices} is out of bounds for length {self.length}")
        else:
            if indices < 0 or indices >= self.length:
                raise IndexError(f"index {indices} is out of bounds for length {self.length}")

        return (self._a * indices + self._b) % self.length
