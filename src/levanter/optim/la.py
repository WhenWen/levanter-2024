import abc
import functools
from dataclasses import dataclass
from typing import Any, NamedTuple, Optional, TypeVar

import equinox as eqx
import jax
import jaxtyping
import optax
from jax import numpy as jnp
from jax.random import PRNGKey
from jaxtyping import PRNGKeyArray

import levanter.tracker
from levanter.optim.config import HessianOptConfig, OptimizerConfig
from levanter.optim.util import hvp, tree_gaussian_like
from levanter.utils.jax_utils import parameter_count, tree_filter_like


@OptimizerConfig.register_subclass("la")
@dataclass
class LAConfig(OptimizerConfig):
    beta1: float = 0.95
    # cf https://docs.mosaicml.com/projects/composer/en/latest/api_reference/generated/composer.optim.DecoupledAdamW.html
    # https://x.com/giffmana/status/1692641748445438301
    beta2: float = 0.95
    gamma: float = 0.025
    epsilon: float = 1e-8
    max_grad_norm: Optional[float] = 1.0

    def build(self, num_train_steps):
        """Creates the optimizer"""
        # indirection makes it work with optax.inject_hyperparams so we can log the learning rate
        def _optimizer(learning_rate):
            components = []

            if self.max_grad_norm:
                components.append(optax.clip_by_global_norm(self.max_grad_norm))

            components.append(scale_by_la(self.beta1, self.beta2, self.gamma, self.epsilon))

            if self.weight_decay > 0:
                components.append(optax.add_decayed_weights(self.weight_decay, self.build_weight_decay_mask()))

            # - learning rate for descent
            components.append(optax.scale(-learning_rate))

            optimizer = optax.chain(*components)

            return optimizer

        return optax.inject_hyperparams(_optimizer)(learning_rate=self.lr_scheduler(num_train_steps))
from optax import tree_utils as otu
import jax
import jax.numpy as jnp
from jax import jit


import chex

class ScaleByLAState(NamedTuple):
  """State for the Mars algorithm."""
  count: chex.Array  # shape=(), dtype=jnp.int32.
  mu: optax.Updates
  nu: optax.Updates
  

def scale_by_la(
    b1: float = 0.9,
    b2: float = 0.999,
    gamma: float = 0.05,
    eps: float = 1e-8,
    eps_root: float = 0.0,
    mu_dtype: Optional[Any] = None,
) -> optax.GradientTransformation:
  r"""Rescale updates according to the Adam algorithm.

  See :func:optax.adam for more details.

  Args:
    b1: Decay rate for the exponentially weighted average of grads.
    b2: Decay rate for the exponentially weighted average of squared grads.
    eps: Term added to the denominator to improve numerical stability.
    eps_root: Term added to the denominator inside the square-root to improve
      numerical stability when backpropagating gradients through the rescaling.
    mu_dtype: Optional dtype to be used for the first order accumulator; if
      None then the dtype is inferred from params and updates.
    nesterov: Whether to use Nesterov momentum. The variant of Adam with
      Nesterov momentum is described in [Dozat 2016]

  Returns:
    A :class:optax.GradientTransformation object.
  """

  mu_dtype = jax.dtypes.canonicalize_dtype(mu_dtype)

  def init_fn(params):
    mu = otu.tree_zeros_like(params, dtype=mu_dtype)  # First moment
    nu = otu.tree_zeros_like(params)  # Second moment
    return ScaleByLAState(count=jnp.zeros([], jnp.int32), mu=mu, nu=nu)

  def update_fn(updates, state, params=None):        
    mu = otu.tree_update_moment(updates, state.mu, b1, 1)
    nu = otu.tree_update_moment_per_elem_norm(updates, state.nu, b2, 2)
    count_inc = optax.safe_increment(state.count)
    mu_hat = jax.tree.map(
        lambda m, g:  None if m is None else gamma * g + (1 - gamma) * m,
        mu,
        updates,
    )
    mu_hat = otu.tree_bias_correction(mu_hat, b1, count_inc)
    nu_hat = otu.tree_bias_correction(nu, b2, count_inc)
    adam_updates = jax.tree.map(
        lambda m, v: None if m is None else m / (jnp.sqrt(v + eps_root) + eps),
        mu_hat,
        nu_hat,
        is_leaf=lambda x: x is None,
    )
    mu = otu.tree_cast(mu, mu_dtype)
    return adam_updates, ScaleByLAState(count=count_inc, mu=mu, nu=nu)
  return optax.GradientTransformation(init_fn, update_fn)