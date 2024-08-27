import equinox
import jax
import jax.random
import optax
import pytest

import haliax as hax

import levanter.tracker
from levanter.callbacks import eval_loss_loop
from levanter.data.dataset import ShardableDataset
from levanter.data.mixture import MixtureDataset
from levanter.trainer import Trainer, TrainerConfig
from levanter.utils.jax_utils import key_iterator
from levanter.utils.py_utils import non_caching_cycle


class Example(equinox.Module):
    x: hax.NamedArray
    y: hax.NamedArray


Block = hax.Axis("Block", 1024)


class LogitDataset(ShardableDataset[Example]):
    def __init__(self, W, noise, x_mask, x_bias, *, key):
        self.W = W
        self.noise = noise
        self.x_mask = x_mask
        self.x_bias = x_bias
        self.key = key

    def __iter__(self):
        key_iter = key_iterator(self.key)
        Dim = self.W.axes[0]
        while True:
            x_block = hax.random.normal(next(key_iter), (Block, Dim)) * self.x_mask + self.x_bias
            noise = hax.random.normal(next(key_iter), (Block,)) * self.noise
            y_block = (hax.nn.sigmoid(hax.dot(x_block, self.W, axis=Dim) + noise) > 0.5).astype(float)
            for i in range(Block.size):
                yield Example(x=x_block[Block, i], y=y_block[Block, i])

    def shard(self, shard_id: int, num_shards: int):
        return LogitDataset(self.W, self.noise, self.x_mask, self.x_bias, key=jax.random.fold_in(self.key, shard_id))


@pytest.mark.slow
def test_estimate_mixture_weights():
    # we create 3 simple logistic regression datasets
    # 1. x is moderately predictive of y (y ~ [0, 0.5, 0.5] x + N(0, noise^2) > 0.5)
    # 2. x is not predictive of y at all, y is highly random (y ~ N(0, 1))
    # 3. x is highly predictive of y, but it's very easy (y = sigmoid([1, 0, 0] x > 0.5)

    Dim = hax.Axis("Dim", 5)
    Batch = hax.Axis("Batch", 32)

    keys = key_iterator(0)

    # W = hax.random.normal(next(keys), (Dim,))
    W1 = hax.named([0.0, 0.5, 0.5, 0.0, 0.0], (Dim,))
    x1_mask = hax.named([0.0, 1.0, 1.0, 0.0, 0.0], (Dim,))
    W2 = hax.named([0.0, 0.0, 0.0, 0.0, 0.0], (Dim,))
    x2_mask = hax.named([0.0, 0.0, 0.0, 1.0, 1.0], (Dim,))
    W3 = hax.named([1.0, 0.0, 0.0, 0.0, 0.0], (Dim,))
    x3_mask = hax.named([1.0, 0.0, 0.0, 0.0, 0.0], (Dim,))
    x3_bias = hax.named([1.0, 0.0, 0.0, 0.0, 0.0], (Dim,))

    # y = sigmoid(Wx + b + N(0, noise^2)) > 0.5
    ds1 = LogitDataset(W1, 0.1, x1_mask, 0.0, key=next(keys))
    ds2 = LogitDataset(W2, 2.0, x2_mask, 0.0, key=next(keys))
    ds3 = LogitDataset(W3, 0.05, x3_mask, x3_bias, key=next(keys))

    # TODO: remove key as a requirement for models
    def compute_loss_fn(model, example, reduction=hax.mean, reduction_axis=None, key=None):
        del key
        y_pred = model(example.x)
        return hax.nn.binary_cross_entropy_loss(y_pred, example.y, reduction=reduction, reduction_axis=reduction_axis)

    tiny_trainer_config = TrainerConfig(
        num_train_steps=600,
        train_batch_size=Batch.size,
        tracker=(),
        id="kmaklfmaf",
        per_device_parallelism=Batch.size // len(jax.devices()),
    )

    optimizer = optax.adam(1e-2)

    trainer = Trainer(tiny_trainer_config, optimizer, compute_loss_fn)

    def fit_to_dataset(dataset):
        initial_model = init_model()
        with trainer:
            state = trainer.initial_state(next(keys), model=initial_model)
            loader = trainer.replicated_loader(dataset, Batch)
            loader = non_caching_cycle(loader)

            loss = 0.0

            # state = trainer.train(state, loader, run_hooks=False)
            for state in trainer.training_steps(state, loader, run_hooks=False):
                if state.step >= 200:
                    loss += state.loss

            return state.model, (loss / (state.step - 200))

    model_key = next(keys)

    def init_model():
        return hax.nn.Linear.init(
            Dim,
            (),
            use_bias=True,
            key=model_key,
            out_first=True,
        )

    m1, loss1 = fit_to_dataset(ds1)
    m2, loss2 = fit_to_dataset(ds2)
    m3, loss3 = fit_to_dataset(ds3)

    assert loss3 < loss1 < loss2

    datasets = {"d1": ds1, "d2": ds2, "d3": ds3}

    ref_model, ref_loss = fit_to_dataset(
        MixtureDataset(datasets, weights={k: 1 / 3.0 for k in datasets.keys()}, key=next(keys))
    )

    # let's see the loss on each dataset
    l1_ref = eval_loss_loop(
        compute_loss_fn, ref_model, trainer.replicated_loader(ds1, Batch), max_batches=10, name="d1"
    )
    l2_ref = eval_loss_loop(
        compute_loss_fn, ref_model, trainer.replicated_loader(ds2, Batch), max_batches=10, name="d2"
    )
    l3_ref = eval_loss_loop(
        compute_loss_fn, ref_model, trainer.replicated_loader(ds3, Batch), max_batches=10, name="d3"
    )

    assert l3_ref < l1_ref < l2_ref

    from levanter.doremi import estimate_mixture_weights
    from levanter.tracker import NoopTracker

    with levanter.tracker.current_tracker(NoopTracker()):
        w = estimate_mixture_weights(
            initial_proxy=init_model(),
            ref=ref_model,
            data_sources=datasets,
            trainer_config=tiny_trainer_config,
            key=next(keys),
            loss_fn=compute_loss_fn,
        )

    w1 = w["d1"]
    w2 = w["d2"]
    w3 = w["d3"]

    assert w1 > w3 > w2, (w1, w2, w3)
    assert abs(w1 + w2 + w3 - 1.0) < 1e-3
    assert w2 < 0.05  # the noise distribution should get a very low weight
