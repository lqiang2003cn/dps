import tensorflow as tf
import numpy as np

from dps import cfg
from dps.register import RegisterBank
from dps.environment import (
    RegressionDataset, RegressionEnv, CompositeEnv, InternalEnv)
from dps.utils import DataContainer, Param, Config
from dps.utils.tf import extract_glimpse_numpy_like, resize_image_with_crop_or_pad
from dps.envs.grid_arithmetic import GridArithmeticDataset


def build_env():
    train = SimpleGridArithmeticDataset(n_examples=cfg.n_train)
    val = SimpleGridArithmeticDataset(n_examples=cfg.n_val)
    test = SimpleGridArithmeticDataset(n_examples=cfg.n_val)

    external = RegressionEnv(train, val, test)

    if cfg.ablation == 'easy':
        internal = SimpleGridArithmeticEasy()
    else:
        internal = SimpleGridArithmetic()

    return CompositeEnv(external, internal)


config = Config(
    build_env=build_env,

    reductions="A:sum M:prod X:max N:min",
    arithmetic_actions="+ * max min +1",

    curriculum=[{}],
    op_loc=(0, 0),
    start_loc=(0, 0),
    base=10,
    threshold=0.04,
    T=30,
    min_digits=2,
    max_digits=3,
    shape=(2, 2),

    n_train=10000,
    n_val=500,

    show_op=True,
    reward_window=0.499,
    salience_action=True,
    salience_shape=(2, 2),
    initial_salience=False,
    visible_glimpse=False,
    final_reward=True,

    ablation='easy',

    log_name='grid_arithmetic',
    render_rollouts=None,
)


class SimpleGridArithmeticDataset(RegressionDataset):
    reductions = Param()
    shape = Param()
    min_digits = Param()
    max_digits = Param()
    base = Param()
    n_examples = Param()
    op_loc = Param()
    loss_type = Param("2-norm")
    largest_digit = Param(9)
    show_op = Param(True)

    reductions_dict = {
        "sum": sum,
        "prod": np.product,
        "max": max,
        "min": min,
        "len": len,
    }

    def __init__(self, **kwargs):
        assert 1 <= self.base <= 10
        assert self.min_digits <= self.max_digits
        assert np.product(self.shape) >= self.max_digits + 1

        if ":" not in self.reductions:
            self.reductions = {'A': self.reductions_dict[self.reductions.strip()]}
            self.show_op = False
        else:
            _reductions = {}
            for pair in self.reductions.split():
                char, key = pair.split(':')
                _reductions[char] = self.reductions_dict[key]
            self.reductions = _reductions

        digits = list(range(self.base))
        digit_reps = DataContainer(digits, digits)

        symbols = np.arange(len(self.reductions))
        symbol_reps = DataContainer(symbols+10, symbols)

        order = sorted(self.reductions)
        symbol_map = {symbol: i for i, symbol in enumerate(order)}
        reductions = {symbol_map[k]: v for k, v in self.reductions.items()}

        blank_element = -1 * np.ones((1, 1))

        x, y = GridArithmeticDataset.make_dataset(
            self.shape, self.min_digits, self.max_digits, self.base,
            blank_element, digit_reps, symbol_reps,
            reductions, self.n_examples, self.op_loc, self.show_op,
            one_hot_output=self.loss_type == "xent", largest_digit=self.largest_digit)

        super(SimpleGridArithmeticDataset, self).__init__(x, y)


class SimpleGridArithmetic(InternalEnv):
    _action_names = ['>', '<', 'v', '^', 'classify_digit', 'classify_op']

    @property
    def input_shape(self):
        return self.shape

    arithmetic_actions = Param()
    shape = Param()
    base = Param()
    start_loc = Param()
    visible_glimpse = Param()
    salience_action = Param()
    salience_shape = Param((2, 2))
    initial_salience = Param()

    arithmetic_actions_dict = {
        '+': lambda acc, digit: acc + digit,
        '-': lambda acc, digit: acc - digit,
        '*': lambda acc, digit: acc * digit,
        '/': lambda acc, digit: acc / digit,
        'max': lambda acc, digit: tf.maximum(acc, digit),
        'min': lambda acc, digit: tf.minimum(acc, digit),
        '+1': lambda acc, digit: acc + 1,
        '-1': lambda acc, digit: acc - 1,
    }

    def __init__(self, **kwargs):
        _arithmetic_actions = {}
        for key in self.arithmetic_actions.split():
            _arithmetic_actions[key] = self.arithmetic_actions_dict[key]
        self.arithmetic_actions = _arithmetic_actions

        if self.salience_action:
            self.action_names = (
                self._action_names +
                ['update_salience'] +
                sorted(self.arithmetic_actions.keys())
            )
        else:
            self.action_names = self._action_names + sorted(self.arithmetic_actions.keys())

        self.actions_dim = len(self.action_names)
        self._init_rb()

        super(SimpleGridArithmetic, self).__init__()

    def _init_rb(self):
        values = (
            [0., 0., -1., 0., 0., -1.] +
            [-1. * np.ones(np.product(self.salience_shape))] +
            [0.]
        )

        min_values = [0, 10, 0, 0, 0, 0] + [-1.] * np.product(self.salience_shape)
        max_values = (
            [9, 12, 999, self.shape[1], self.shape[0], self.actions_dim] +
            [1.] * np.product(self.salience_shape)
        )

        if self.visible_glimpse:
            min_values.append(-1.)
            max_values.append(self.base + len(self.reductions))

            self.rb = RegisterBank(
                'GridArithmeticRB',
                'digit op acc fovea_x fovea_y prev_action salience glimpse', '', values=values,
                output_names='acc', min_values=min_values, max_values=max_values
            )
        else:
            self.rb = RegisterBank(
                'GridArithmeticRB',
                'digit op acc fovea_x fovea_y prev_action salience', 'glimpse', values=values,
                output_names='acc', min_values=min_values, max_values=max_values
            )

    def _build_update_glimpse(self, fovea_y, fovea_x):
        top_left = tf.concat([fovea_y, fovea_x], axis=-1)
        inp = self.input_ph[..., None]
        glimpse = extract_glimpse_numpy_like(inp, (1, 1), top_left)
        glimpse = tf.reshape(glimpse, (-1, 1), name="glimpse")
        return glimpse

    def _build_update_salience(self, update_salience, salience, fovea_y, fovea_x):
        correction = -tf.ceil(np.array(self.salience_shape, dtype='f') / 2.) + 1
        top_left = tf.concat([fovea_y, fovea_x], axis=-1) + correction + np.array(self.pad_offset)

        inp = tf.cast(tf.equal(self.padded_input, -1), tf.float32)

        glimpse = extract_glimpse_numpy_like(inp, self.salience_shape, top_left)
        new_salience = tf.reshape(glimpse, (-1, np.product(self.salience_shape)))

        salience = (1-update_salience) * salience + update_salience * new_salience
        return salience

    def _build_update_storage(self, glimpse, prev_digit, classify_digit, prev_op, classify_op):
        digit = tf.where(
            tf.logical_and(glimpse >= 0, glimpse < 10),
            glimpse, -1 * tf.ones(tf.shape(glimpse)))

        new_digit = (1 - classify_digit) * prev_digit + classify_digit * digit

        op = tf.where(glimpse >= 10, glimpse, -1 * tf.ones(tf.shape(glimpse)))

        new_op = (1 - classify_op) * prev_op + classify_op * op

        return new_digit, new_op

    def _build_update_fovea(self, right, left, down, up, fovea_y, fovea_x):
        fovea_x = (1 - right - left) * fovea_x + \
            right * (fovea_x + 1) + \
            left * (fovea_x - 1)
        fovea_y = (1 - down - up) * fovea_y + \
            down * (fovea_y + 1) + \
            up * (fovea_y - 1)
        fovea_y = tf.clip_by_value(fovea_y, 0, self.shape[0]-1)
        fovea_x = tf.clip_by_value(fovea_x, 0, self.shape[1]-1)
        return fovea_y, fovea_x

    def _build_return(
            self, digit, op, acc, fovea_x, fovea_y,
            prev_action, salience, glimpse):

        with tf.name_scope("GridArithmetic"):
            new_registers = self.rb.wrap(
                digit=tf.identity(digit, "digit"),
                op=tf.identity(op, "op"),
                acc=tf.identity(acc, "acc"),
                fovea_x=tf.identity(fovea_x, "fovea_x"),
                fovea_y=tf.identity(fovea_y, "fovea_y"),
                prev_action=tf.identity(prev_action, "prev_action"),
                salience=tf.identity(salience, "salience"),
                glimpse=glimpse)

        rewards = self.build_rewards(new_registers)

        return (
            tf.fill((tf.shape(digit)[0], 1), 0.0),
            rewards,
            new_registers)

    def build_init(self, r):
        self.build_placeholders(r)
        self.pad_offset = (
            int(np.floor(self.salience_shape[0] / 2)),
            int(np.floor(self.salience_shape[1] / 2))
        )
        target_height = int(self.input_ph.shape[1]) + 2 * self.pad_offset[0]
        target_width = int(self.input_ph.shape[2]) + 2 * self.pad_offset[1]
        inp = self.input_ph[..., None]
        self.padded_input = resize_image_with_crop_or_pad(inp, target_height, target_width)

        _digit, _op, _acc, _fovea_x, _fovea_y, _prev_action, _salience, _glimpse = self.rb.as_tuple(r)

        batch_size = tf.shape(self.input_ph)[0]

        # init fovea
        if self.start_loc is not None:
            fovea_y = tf.fill((batch_size, 1), self.start_loc[0])
            fovea_x = tf.fill((batch_size, 1), self.start_loc[1])
        else:
            fovea_y = tf.random_uniform(
                tf.shape(fovea_y), 0, self.shape[0], dtype=tf.int32)
            fovea_x = tf.random_uniform(
                tf.shape(fovea_x), 0, self.shape[1], dtype=tf.int32)

        fovea_y = tf.cast(fovea_y, tf.float32)
        fovea_x = tf.cast(fovea_x, tf.float32)

        glimpse = self._build_update_glimpse(fovea_y, fovea_x)

        salience = _salience
        if self.initial_salience:
            salience = self._build_update_salience(1.0, _salience, _fovea_y, _fovea_x)

        digit = -1 * tf.ones((batch_size, 1), dtype=tf.float32)
        op = -1 * tf.ones((batch_size, 1), dtype=tf.float32)
        acc = -1 * tf.ones((batch_size, 1), dtype=tf.float32)

        _, _, new_r = self._build_return(digit, op, acc, fovea_x, fovea_y, _prev_action, salience, glimpse)
        return new_r

    def build_step(self, t, r, a):
        _digit, _op, _acc, _fovea_x, _fovea_y, _prev_action, _salience, _glimpse = self.rb.as_tuple(r)

        (right, left, down, up, classify_digit, classify_op,
            update_salience, *arithmetic_actions) = self.unpack_actions(a)

        salience = _salience
        if self.salience_action:
            salience = self._build_update_salience(update_salience, _salience, _fovea_y, _fovea_x)

        digit = tf.zeros_like(_digit)
        acc = tf.zeros_like(_acc)

        original_factor = tf.ones_like(right)
        for key, action in zip(sorted(self.arithmetic_actions), arithmetic_actions):
            original_factor -= action
            acc += action * self.arithmetic_actions[key](_acc, _digit)
        acc += original_factor * _acc

        acc = tf.clip_by_value(acc, -1000.0, 1000.0)

        digit, op = self._build_update_storage(_glimpse, _digit, classify_digit, _op, classify_op)
        fovea_y, fovea_x = self._build_update_fovea(right, left, down, up, _fovea_y, _fovea_x)
        glimpse = self._build_update_glimpse(fovea_y, fovea_x)

        action = tf.cast(tf.reshape(tf.argmax(a, axis=1), (-1, 1)), tf.float32)

        return self._build_return(digit, op, acc, fovea_x, fovea_y, action, salience, glimpse)


class SimpleGridArithmeticEasy(SimpleGridArithmetic):
    def build_step(self, t, r, a):
        _digit, _op, _acc, _fovea_x, _fovea_y, _prev_action, _salience, _glimpse = self.rb.as_tuple(r)

        (right, left, down, up, classify_digit, classify_op,
            update_salience, *arithmetic_actions) = self.unpack_actions(a)

        salience = _salience
        if self.salience_action:
            salience = self._build_update_salience(
                update_salience, _salience, _fovea_y, _fovea_x)

        op = tf.where(_glimpse >= 10, _glimpse, -1 * tf.ones(tf.shape(_glimpse)))
        op = (1 - classify_op) * _op + classify_op * op

        orig_digit_factor = tf.ones_like(right) - classify_digit
        for action in arithmetic_actions:
            orig_digit_factor -= action

        digit = tf.where(
            tf.logical_and(_glimpse >= 0, _glimpse < 10),
            _glimpse, -1 * tf.ones(tf.shape(_glimpse)))
        digit = (1 - classify_digit) * _digit + classify_digit * digit

        orig_acc_factor = tf.ones_like(right)
        acc = tf.zeros_like(_acc)
        for key, action in zip(sorted(self.arithmetic_actions), arithmetic_actions):
            orig_acc_factor -= action
            # Its crucial that we use `digit` here and not `_digit`
            acc += action * self.arithmetic_actions[key](_acc, digit)
        acc += orig_acc_factor * _acc

        acc = tf.clip_by_value(acc, -1000.0, 1000.0)

        fovea_y, fovea_x = self._build_update_fovea(right, left, down, up, _fovea_y, _fovea_x)
        glimpse = self._build_update_glimpse(fovea_y, fovea_x)

        action = tf.cast(tf.reshape(tf.argmax(a, axis=1), (-1, 1)), tf.float32)

        return self._build_return(digit, op, acc, fovea_x, fovea_y, action, salience, glimpse)