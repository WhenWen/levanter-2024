import os
import tempfile

import jax
import pytest

import haliax

import levanter.main.eval_lm as eval_lm
import tiny_test_corpus
from levanter.checkpoint import save_checkpoint
from levanter.distributed import RayConfig
from levanter.models.gpt2 import Gpt2LMHeadModel
from levanter.tracker.wandb import WandbConfig
from levanter.trainer_state import TrainerState
from test_utils import skip_if_no_torch


@pytest.mark.entry
def test_eval_lm():
    # just testing if eval_lm has a pulse
    # save a checkpoint
    model_config = eval_lm.Gpt2Config(
        num_layers=2,
        num_heads=2,
        seq_len=64,
        hidden_dim=32,
        use_flash_attention=True,
    )

    with tempfile.TemporaryDirectory() as f:
        try:
            data_config, _ = tiny_test_corpus.construct_small_data_cache(f)
            tok = data_config.the_tokenizer
            Vocab = haliax.Axis("vocab", len(tok))
            model = Gpt2LMHeadModel.init(Vocab, model_config, key=jax.random.PRNGKey(0))

            state = TrainerState(0, model, model, jax.random.PRNGKey(0), None, True, None, None)

            save_checkpoint(state, 0, f"{f}/ckpt")

            config = eval_lm.EvalLmConfig(
                data=data_config,
                model=model_config,
                trainer=eval_lm.TrainerConfig(
                    per_device_eval_parallelism=len(jax.devices()),
                    max_eval_batches=1,
                    wandb=WandbConfig(mode="disabled"),
                    require_accelerator=False,
                    ray=RayConfig(auto_start_cluster=False),
                ),
                checkpoint_path=f"{f}/ckpt",
            )
            eval_lm.main(config)
        finally:
            try:
                os.unlink("wandb")
            except Exception:
                pass


@pytest.mark.entry
@skip_if_no_torch
def test_eval_lm_from_hf():
    # just testing if eval_lm has a pulse
    # save a checkpoint
    model_config = eval_lm.Gpt2Config(
        num_layers=2,
        num_heads=2,
        seq_len=1024,
        hidden_dim=2,
        use_flash_attention=True,
    )

    with tempfile.TemporaryDirectory() as f:
        try:
            data_config, _ = tiny_test_corpus.construct_small_data_cache(f)
            tok = data_config.the_tokenizer
            Vocab = haliax.Axis("vocab", len(tok))
            model = Gpt2LMHeadModel.init(Vocab, model_config, key=jax.random.PRNGKey(0))

            state = TrainerState(0, model, model, jax.random.PRNGKey(0), None, True, None, None)

            save_checkpoint(state, 0, f"{f}/ckpt")

            config = eval_lm.EvalLmConfig(
                data=data_config,
                model=model_config,
                hf_checkpoint="sshleifer/tiny-gpt2",
                trainer=eval_lm.TrainerConfig(
                    per_device_eval_parallelism=len(jax.devices()),
                    max_eval_batches=1,
                    wandb=WandbConfig(mode="disabled"),
                    require_accelerator=False,
                    ray=RayConfig(auto_start_cluster=False),
                ),
                checkpoint_path=f"{f}/ckpt",
            )
            eval_lm.main(config)
        finally:
            try:
                os.unlink("wandb")
            except Exception:
                pass
