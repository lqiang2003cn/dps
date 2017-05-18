import numpy as np
import tensorflow as tf
import os

from dps.updater import DifferentiableUpdater
from dps.reinforce import REINFORCE
from dps.qlearning import QLearning
from dps.policy import SoftmaxSelect, EpsilonGreedySelect, GumbelSoftmaxSelect
from dps.utils import Config, CompositeCell, FeedforwardCell, MLP, DpsConfig
from dps.experiments import (
    arithmetic, simple_addition, pointer_following,
    hard_addition, lifted_addition, translated_mnist, mnist_arithmetic)


class DefaultConfig(DpsConfig):
    seed = 12

    preserve_policy = True  # Whether to use the policy learned on the last stage of the curriculum for each new stage.

    optimizer_class = tf.train.RMSPropOptimizer
    updater_class = None

    power_through = True  # Whether to complete the entire curriculum, even if threshold not reached.
    max_steps = 100
    batch_size = 100
    n_train = 10000
    n_val = 200
    n_test = 0

    threshold = 1e-2
    patience = np.inf

    display_step = 100
    eval_step = 10
    checkpoint_step = 1000
    n_controller_units = 32

    controller_func = staticmethod(
        lambda n_actions: CompositeCell(tf.contrib.rnn.LSTMCell(num_units=DefaultConfig.n_controller_units),
                                        MLP(),
                                        n_actions))

    lr_start, lr_denom, lr_decay = 0.001, 1000, 0.96
    exploration_start, exploration_denom, exploration_decay = 10.0, 1000, 0.96

    test_time_explore = None

    max_grad_norm = 0.0
    l2_norm_penalty = 0.0
    gamma = 1.0
    reward_window = 0.1

    n_auxiliary_tasks = 0
    auxiliary_coef = 0

    debug = False
    verbose = False
    display = False
    save_display = False
    path = os.getcwd()
    max_time = 0


class DiffConfig(Config):
    updater_class = DifferentiableUpdater
    test_time_explore = None
    # action_selection = SoftmaxSelect()
    action_selection = GumbelSoftmaxSelect(hard=0)
    max_grad_norm = 1.0
    patience = np.inf
    T = 10

    noise_start, noise_denom, noise_decay = 0.1, 1000, 0.96


class ReinforceConfig(Config):
    updater_class = REINFORCE
    action_selection = SoftmaxSelect()
    test_time_explore = None
    patience = np.inf

    entropy_start, entropy_denom, entropy_decay = 0.01, 1000, 0.96
    lr_start, lr_denom, lr_decay = 0.01, 1000, 0.96
    noise_start = None


class QLearningConfig(Config):
    updater_class = QLearning
    action_selection = EpsilonGreedySelect()
    double = False

    entropy_start, entropy_denom, entropy_decay = 0.2, 1000, 0.98
    lr_start, lr_denom, lr_decay = 0.01, 1000, 1.0

    replay_max_size = 100000
    replay_threshold = -0.5
    replay_proportion = None

    target_update_rate = 0.01
    recurrent = True
    patience = np.inf
    batch_size = 10
    test_time_explore = 0.0

    l2_norm_penalty = 0.0
    max_grad_norm = 0.0

    controller_func = staticmethod(
        lambda n_actions: CompositeCell(tf.contrib.rnn.LSTMCell(num_units=64),
                                        MLP(),
                                        n_actions))
    # controller_func = staticmethod(
    #     lambda n_actions: FeedforwardCell(MLP([10, 10], activation_fn=tf.nn.sigmoid), n_actions))


class DebugConfig(Config):
    max_steps = 4
    batch_size = 2
    n_train = 10
    n_val = 10
    n_test = 0

    display_step = 1
    eval_step = 1

    debug = True


class ArithmeticConfig(DefaultConfig):
    T = 3
    curriculum = [
        dict(order=[0, 1], T=2),
        dict(order=[0, 1, 0], T=3),
        dict(order=[0, 1, 0, 1], T=4)]
    controller_func = staticmethod(
        lambda n_actions: CompositeCell(tf.contrib.rnn.LSTMCell(num_units=32),
                                        MLP(),
                                        n_actions))
    log_name = 'arithmetic'
    trainer = arithmetic.ArithmeticTrainer()
    visualize = arithmetic.visualize


class SimpleAdditionConfig(DefaultConfig):
    T = 30
    curriculum = [
        dict(width=1, n_digits=10),
        dict(width=2, n_digits=10),
        dict(width=3, n_digits=10),
    ]

    # curriculum = [
    #     dict(width=1, n_digits=10),
    #     dict(width=2, n_digits=10),
    #     dict(width=3, n_digits=10),
    #     dict(width=4, n_digits=10),
    #     dict(width=5, n_digits=10),
    #     dict(width=6, n_digits=10),
    #     dict(width=7, n_digits=10),
    #     dict(width=8, n_digits=10),
    #     dict(width=9, n_digits=10),
    #     dict(width=9, n_digits=10, T=40),
    #     dict(width=10, n_digits=10, T=40),
    #     dict(width=11, n_digits=10, T=40),
    #     dict(width=12, n_digits=10, T=50),
    #     dict(width=13, n_digits=10, T=50),
    #     dict(width=15, n_digits=10, T=60),
    #     dict(width=17, n_digits=10, T=70),
    #     dict(width=20, n_digits=10, T=80),
    #     dict(width=25, n_digits=10, T=90),
    #     dict(width=30, n_digits=10, T=100),
    #     dict(width=35, n_digits=10, T=110),
    # ]

    log_name = 'simple_addition'

    trainer = simple_addition.SimpleAdditionTrainer()
    visualize = simple_addition.visualize


class PointerConfig(DefaultConfig):
    T = 30
    curriculum = [
        dict(width=1, n_digits=10),
        dict(width=2, n_digits=10)]
    log_name = 'pointer'

    trainer = pointer_following.PointerTrainer()
    visualize = pointer_following.visualize


class HardAdditionConfig(DefaultConfig):
    T = 40
    curriculum = [
        dict(height=2, width=3, n_digits=2, entropy_start=1.0),
        dict(height=2, width=3, n_digits=2, entropy_start=0.0)]
    log_name = 'hard_addition'
    trainer = hard_addition.HardAdditionTrainer()
    visualize = hard_addition.visualize
    preserve_policy = False


class LiftedAdditionConfig(DefaultConfig):
    T = 30
    curriculum = [
        dict(width=1, n_digits=10),
        dict(width=2, n_digits=10),
        dict(width=3, n_digits=10),
        dict(width=4, n_digits=10),
        dict(width=5, n_digits=10)]
    log_name = 'lifted_addition'

    trainer = lifted_addition.LiftedAdditionTrainer()
    visualize = lifted_addition.visualize


class TranslatedMnistConfig(DefaultConfig):
    T = 10
    scaled = True
    curriculum = [
        dict(W=28, N=8, T=4),
        dict(W=28, N=8, T=10),
        dict(W=35, N=8, T=10),
        dict(W=45, N=8, T=10),
        dict(W=55, N=8, T=10),
        dict(W=65, N=8, T=12),
        dict(W=65, N=8, T=15),
        dict(W=65, N=8, T=20),
        dict(W=75, N=8, T=20),
        dict(W=85, N=8, T=20),
        dict(W=95, N=8, T=20)
    ]
    threshold = 0.10
    verbose = 4

    classifier_str = "MLP_50_50"

    @staticmethod
    def build_classifier(inp, outp_size):
        logits = MLP([50, 50], activation_fn=tf.nn.sigmoid)(inp, outp_size)
        return tf.nn.softmax(logits)

    # controller_func = staticmethod(
    #     lambda n_actions: FeedforwardCell(MLP([100, 100], activation_fn=tf.nn.sigmoid), n_actions))
    controller_func = staticmethod(
        lambda n_actions: CompositeCell(tf.contrib.rnn.LSTMCell(num_units=256),
                                        MLP(),
                                        n_actions))
    reward_window = 0.4

    log_name = 'translated_mnist'

    inc_delta = 0.1
    inc_x = 0.1
    inc_y = 0.1

    trainer = translated_mnist.TranslatedMnistTrainer()
    visualize = translated_mnist.visualize


class MnistArithmeticConfig(DefaultConfig):
    curriculum = [
        dict(W=100, N=8, T=10),
        dict(W=100, N=8, T=20),
        dict(W=100, N=8, T=30)
    ]
    simple = False
    threshold = 0.15
    verbose = 4
    base = 2
    n_digits = 2

    classifier_str = "MLP_30_30"

    @staticmethod
    def build_classifier(inp, outp_size):
        logits = MLP([30, 30], activation_fn=tf.nn.sigmoid)(inp, outp_size)
        return tf.nn.softmax(logits)

    # controller_func = staticmethod(
    #     lambda n_actions: FeedforwardCell(MLP([100, 100], activation_fn=tf.nn.sigmoid), n_actions))
    controller_func = staticmethod(
        lambda n_actions: CompositeCell(tf.contrib.rnn.LSTMCell(num_units=256),
                                        MLP(),
                                        n_actions))
    reward_window = 0.5

    log_name = 'mnist_arithmetic'

    inc_delta = 0.1
    inc_x = 0.1
    inc_y = 0.1

    trainer = mnist_arithmetic.MnistArithmeticTrainer()
    visualize = mnist_arithmetic.visualize


algorithms = dict(
    diff=DiffConfig(),
    reinforce=ReinforceConfig(),
    qlearning=QLearningConfig())


tasks = dict(
    arithmetic=ArithmeticConfig(),
    simple_addition=SimpleAdditionConfig(),
    pointer=PointerConfig(),
    hard_addition=HardAdditionConfig(),
    lifted_addition=LiftedAdditionConfig(),
    translated_mnist=TranslatedMnistConfig(),
    mnist_arithmetic=MnistArithmeticConfig())