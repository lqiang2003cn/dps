from dps.train import training_loop
from dps.config import DEFAULT_CONFIG
from dps.rl.algorithms import ppo
from dps.envs import room

config = DEFAULT_CONFIG.copy()
config.update(ppo.config)
config.update(room.config)

config.update(
    max_steps=100000,
    threshold=-1,
)

with config:
    training_loop()
