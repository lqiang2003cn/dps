import tensorflow as tf
import numpy as np

from dps import cfg
from dps.register import RegisterBank
from dps.environment import (
    RegressionDataset, RegressionEnv, CompositeEnv, InternalEnv)
from dps.vision import MnistPretrained, ClassifierFunc, LeNet, MNIST_CONFIG
from dps.utils import DataContainer, Param, Config
from dps.rl.policy import Softmax, Normal, ProductDist, Policy, DiscretePolicy

from mnist_arithmetic import load_emnist


def build_env():
    train = GridArithmeticDataset(n_examples=cfg.n_train)
    val = GridArithmeticDataset(n_examples=cfg.n_val)
    test = GridArithmeticDataset(n_examples=cfg.n_val)

    external = RegressionEnv(train, val, test)

    if cfg.ablation == 'bad_wiring':
        internal = GridArithmeticBadWiring()
    elif cfg.ablation == 'no_classifiers':
        internal = GridArithmeticNoClassifiers()
    elif cfg.ablation == 'no_ops':
        internal = GridArithmeticNoOps()
    elif cfg.ablation == 'no_modules':
        internal = GridArithmeticNoModules()
    elif cfg.ablation == 'easy':
        internal = GridArithmeticEasy()
    else:
        internal = GridArithmetic()

    return CompositeEnv(external, internal)


def build_policy(env, **kwargs):
    if cfg.ablation == 'bad_wiring':
        action_selection = ProductDist(Softmax(11), Normal(), Normal(), Normal())
    elif cfg.ablation == 'no_classifiers':
        action_selection = ProductDist(Softmax(9), Softmax(10, one_hot=0), Softmax(10, one_hot=0), Softmax(10, one_hot=0))
    elif cfg.ablation == 'no_ops':
        action_selection = ProductDist(Softmax(11), Normal(), Normal(), Normal())
    elif cfg.ablation == 'no_modules':
        action_selection = ProductDist(Softmax(11), Normal(), Normal(), Normal())
    else:
        action_selection = Softmax(env.actions_dim)
        return DiscretePolicy(action_selection, env.obs_shape, **kwargs)
    return Policy(action_selection, env.obs_shape, **kwargs)


config = Config(
    build_env=build_env,
    reductions=[
        ('A', lambda x: sum(x)),
        ('M', lambda x: np.product(x)),
        ('X', lambda x: max(x)),
        ('N', lambda x: min(x)),
        ('C', lambda x: len(x)),
    ],

    arithmetic_actions=[
        ('+', lambda acc, digit: acc + digit),
        ('+1', lambda acc, digit: acc + 1),
        # ('*', lambda acc, digit: acc * digit),
        # ('=', lambda acc, digit: digit),
        # ('max', lambda acc, digit: tf.maximum(acc, digit))
        # ('min', lambda acc, digit: tf.minimum(acc, digit))
    ],

    # curriculum=[dict()],
    curriculum=[dict(shape=(3, 1)), dict(shape=(3, 2))],
    mnist=False,
    op_loc=(0, 0),
    start_loc=(0, 0),
    base=10,
    threshold=0.04,
    T=30,
    min_digits=2,
    max_digits=2,
    shape=(3, 2),
    salience_shape=(2, 2),

    n_train=10000,
    n_val=500,

    show_op=True,
    reward_window=0.5,
    salience_action=True,
    initial_salience=False,
    visible_glimpse=False,

    ablation='',  # anything other than "bad_wiring", "no_classifiers", "no_ops", "no_modules" will use the default.

    classifier_str="LeNet2_1024",
    build_classifier=lambda inp, output_size, is_training=False: tf.nn.softmax(
        LeNet(1024, activation_fn=tf.nn.sigmoid)(inp, output_size, is_training)),

    mnist_config=MNIST_CONFIG.copy(
        eval_step=100,
        max_steps=100000,
        patience=np.inf,
        threshold=0.05,
        include_blank=True
    ),

    log_name='grid_arithmetic',
    render_rollouts=None,
)


class GridArithmeticDataset(RegressionDataset):
    mnist = Param()
    reductions = Param()
    shape = Param()
    min_digits = Param()
    max_digits = Param()
    base = Param()
    n_examples = Param()
    op_loc = Param()
    loss_type = Param("2-norm")
    largest_digit = Param(10)
    downsample_factor = Param(1)
    show_op = Param(True)

    def __init__(self, **kwargs):
        assert 1 <= self.base <= 10
        assert self.min_digits <= self.max_digits
        assert np.product(self.shape) >= self.max_digits + 1

        self.s = s = int(28 / self.downsample_factor)

        if callable(self.reductions):
            self.reductions = {'A': self.reductions}
            self.show_op = False
        else:
            self.reductions = dict(self.reductions)

        op_symbols = sorted(self.reductions)

        if self.mnist:
            op_symbols = sorted(self.reductions)
            emnist_x, emnist_y, symbol_map = load_emnist(
                cfg.data_dir, op_symbols, balance=True,
                downsample_factor=self.downsample_factor)
            emnist_x = emnist_x.reshape(-1, s, s)
            emnist_y = np.squeeze(emnist_y, 1)

            reductions = {symbol_map[k]: v for k, v in self.reductions.items()}

            symbol_reps = DataContainer(emnist_x, emnist_y)

            mnist_x, mnist_y, _ = load_emnist(
                cfg.data_dir, list(range(self.base)), balance=True,
                downsample_factor=self.downsample_factor)
            mnist_x = mnist_x.reshape(-1, s, s)
            mnist_y = np.squeeze(mnist_y, 1)

            digit_reps = DataContainer(mnist_x, mnist_y)
            blank_element = np.zeros((s, s))
        else:
            reductions = {i: self.reductions[k] for i, k in enumerate(op_symbols)}
            symbol_values = np.array(sorted(reductions))

            symbol_reps = DataContainer(symbol_values + 10, symbol_values)
            digit_reps = DataContainer(np.arange(self.base), np.arange(self.base))

            blank_element = np.array([[-1]])

        x, y = self.make_dataset(
            self.shape, self.min_digits, self.max_digits, self.base,
            blank_element, symbol_reps, digit_reps,
            reductions, self.n_examples, self.op_loc, self.show_op,
            one_hot_output=self.loss_type == "xent", largest_digit=self.largest_digit)

        super(GridArithmeticDataset, self).__init__(x, y)

    @staticmethod
    def make_dataset(
            shape, min_digits, max_digits, base, blank_element,
            symbol_reps, digit_reps, functions, n_examples, op_loc, show_op,
            one_hot_output, largest_digit):

        if n_examples == 0:
            return (
                np.zeros((0,) + shape + blank_element.shape).astype('f'),
                np.zeros((0, 1)).astype('i'))

        new_X, new_Y = [], []

        size = np.product(shape)

        element_shape = blank_element.shape
        m, n = element_shape
        if op_loc is not None:
            _op_loc = np.ravel_multi_index(op_loc, shape)

        for j in range(n_examples):
            nd = np.random.randint(min_digits, max_digits+1)

            indices = np.random.choice(size, nd+1, replace=False)

            if op_loc is not None and show_op:
                indices[indices == _op_loc] = indices[0]
                indices[0] = _op_loc

            env = np.tile(blank_element, shape)
            locs = zip(*np.unravel_index(indices, shape))
            locs = [(slice(i*m, (i+1)*m), slice(j*n, (j+1)*n)) for i, j in locs]
            op_loc, *digit_locs = locs

            symbol_x, symbol_y = symbol_reps.get_random()
            func = functions[int(symbol_y)]

            if show_op:
                env[op_loc] = symbol_x

            ys = []

            for loc in digit_locs:
                x, y = digit_reps.get_random()
                ys.append(y)
                env[loc] = x

            new_X.append(env)
            y = func(ys)

            if one_hot_output:
                _y = np.zeros(largest_digit+2)
                if y > largest_digit:
                    _y[-1] = 1.0
                else:
                    _y[int(y)] = 1.0
                y = _y

            new_Y.append(y)

        new_X = np.array(new_X).astype('f')

        if one_hot_output:
            new_Y = np.array(new_Y).astype('f')
        else:
            new_Y = np.array(new_Y).astype('i').reshape(-1, 1)

        return new_X, new_Y


class GridArithmetic(InternalEnv):
    _action_names = ['>', '<', 'v', '^', 'classify_digit', 'classify_op', 'no-op']

    @property
    def element_shape(self):
        return (self.s, self.s) if self.mnist else (1, 1)

    @property
    def input_shape(self):
        return tuple(s*e for s, e in zip(self.shape, self.element_shape))

    mnist = Param()
    reductions = Param()
    arithmetic_actions = Param()
    shape = Param()
    base = Param()
    start_loc = Param()
    classification_bonus = Param(0.0)
    downsample_factor = Param(1)
    visible_glimpse = Param()
    salience_shape = Param()
    salience_action = Param()
    initial_salience = Param()

    def __init__(self, **kwargs):
        self.arithmetic_actions = dict(self.arithmetic_actions)
        if self.salience_action:
            self.action_names = self._action_names + ['update_salience'] + sorted(self.arithmetic_actions.keys())
        else:
            self.action_names = self._action_names + sorted(self.arithmetic_actions.keys())

        if self.salience_shape is None:
            self.salience_shape = self.shape

        self.actions_dim = len(self.action_names)
        self.init_classifiers()
        self.init_rb()

        if self.salience_action and self.mnist:
            raise Exception("NotImplemented")

        super(GridArithmetic, self).__init__()

    def init_rb(self):
        values = (
            [0., 0., -1., 0., 0., -1.] +
            [-1 * np.ones(np.product(self.salience_shape), dtype='f')] +
            [np.zeros(np.product(self.element_shape), dtype='f')])

        min_values = [0, 10, 0, 0, 0, 0] + [-1] * np.product(self.salience_shape)
        max_values = [9, 12, 999, self.shape[1], self.shape[0], self.actions_dim] + [1] * np.product(self.salience_shape)

        if self.visible_glimpse:
            min_values.extend([0] * np.product(self.element_shape))

            if self.mnist:
                max_values.extend([1] * np.product(self.element_shape))
            else:
                max_values.extend([12] * np.product(self.element_shape))

            self.rb = RegisterBank(
                'GridArithmeticRB',
                'digit op acc fovea_x fovea_y prev_action salience glimpse', '', values=values,
                output_names='acc', no_display='glimpse' if self.mnist else None,
                min_values=min_values, max_values=max_values
            )
        else:
            self.rb = RegisterBank(
                'GridArithmeticRB',
                'digit op acc fovea_x fovea_y prev_action salience', 'glimpse', values=values,
                output_names='acc', no_display='glimpse' if self.mnist else None,
                min_values=min_values, max_values=max_values
            )

    def init_classifiers(self):
        if self.mnist:
            build_classifier = cfg.build_classifier
            classifier_str = cfg.classifier_str

            digit_config = cfg.mnist_config.copy(
                classes=list(range(self.base)), downsample_factor=self.downsample_factor)

            name = '{}_classes={}_df={}.chk'.format(
                classifier_str, '_'.join(str(s) for s in digit_config.classes), self.downsample_factor)
            digit_pretrained = MnistPretrained(
                None, build_classifier, name=name,
                model_dir='/tmp/dps/mnist_pretrained/',
                var_scope_name='digit_classifier', mnist_config=digit_config,
                downsample_factor=self.downsample_factor)
            self.build_digit_classifier = ClassifierFunc(digit_pretrained, len(digit_config.classes) + 1)

            op_config = cfg.mnist_config.copy(
                classes=self.reductions.keys(), downsample_factor=self.downsample_factor)

            name = '{}_classes={}_df={}.chk'.format(
                classifier_str, '_'.join(str(s) for s in op_config.classes), self.downsample_factor)

            op_pretrained = MnistPretrained(
                None, build_classifier, name=name,
                model_dir='/tmp/dps/mnist_pretrained/',
                var_scope_name='op_classifier', mnist_config=op_config,
                downsample_factor=self.downsample_factor)
            self.build_op_classifier = ClassifierFunc(op_pretrained, len(op_config.classes) + 1)

        else:

            self.build_digit_classifier = lambda x: tf.where(
                tf.logical_and(x >= 0, x < 10), x, -1 * tf.ones(tf.shape(x)))
            self.build_op_classifier = lambda x: tf.where(
                x >= 10, x, -1 * tf.ones(tf.shape(x)))

    def build_init_glimpse(self, batch_size, inp, fovea_y, fovea_x):
        centres = tf.concat([fovea_y, fovea_x], axis=-1) * np.array(self.element_shape)
        centres += np.ceil(np.array(self.element_shape) / 2)
        inp = tf.expand_dims(inp, -1)
        glimpse = tf.image.extract_glimpse(inp, self.element_shape, centres, normalized=False, centered=False)
        glimpse = tf.reshape(glimpse, (-1, np.product(self.element_shape)), name="glimpse")
        return glimpse

    def build_init_storage(self, batch_size):
        digit = -1 * tf.ones((batch_size, 1))
        op = -1 * tf.ones((batch_size, 1))
        return digit, op

    def build_init_fovea(self, batch_size, fovea_y, fovea_x):
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
        return fovea_y, fovea_x

    def build_init(self, r):
        self.build_placeholders(r)

        digit, op, acc, fovea_x, fovea_y, prev_action, salience, glimpse = self.rb.as_tuple(r)

        if self.initial_salience:
            centres = tf.concat([fovea_y, fovea_x], axis=-1) * np.array(self.element_shape)
            centres += np.ceil(np.array(self.element_shape) / 2)
            inp = tf.cast(tf.equal(tf.expand_dims(self.input_ph, -1), -1), tf.float32)
            new_salience = tf.image.extract_glimpse(inp, self.salience_shape, centres, normalized=False, centered=False)
            new_salience = tf.reshape(new_salience, (tf.shape(digit)[0], -1))
            salience = 0 * salience + new_salience

        acc = -1 * tf.ones(tf.shape(digit), dtype=tf.float32)

        batch_size = tf.shape(self.input_ph)[0]

        digit, op = self.build_init_storage(batch_size)

        fovea_y, fovea_x = self.build_init_fovea(batch_size, fovea_y, fovea_x)
        glimpse = self.build_init_glimpse(batch_size, self.input_ph, fovea_y, fovea_x)

        _, _, ret = self.build_return(digit, op, acc, fovea_x, fovea_y, prev_action, salience, glimpse)
        return ret

    def build_update_glimpse(self, inp, fovea_y, fovea_x):
        centres = tf.concat([fovea_y, fovea_x], axis=-1) * np.array(self.element_shape)
        centres += np.ceil(np.array(self.element_shape) / 2)
        inp = tf.expand_dims(inp, -1)
        glimpse = tf.image.extract_glimpse(inp, self.element_shape, centres, normalized=False, centered=False)
        glimpse = tf.reshape(glimpse, (-1, np.product(self.element_shape)), name="glimpse")
        return glimpse

    def build_update_storage(self, glimpse, digit, classify_digit, op, classify_op):
        digit_classification = self.build_digit_classifier(glimpse)
        digit_vision = tf.cast(digit_classification, tf.float32)
        digit = (1 - classify_digit) * digit + classify_digit * digit_vision

        op_classification = self.build_op_classifier(glimpse)
        op_vision = tf.cast(op_classification, tf.float32)
        op = (1 - classify_op) * op + classify_op * op_vision
        return digit, op

    def build_update_fovea(self, right, left, down, up, fovea_y, fovea_x):
        fovea_x = (1 - right - left) * fovea_x + \
            right * (fovea_x + 1) + \
            left * (fovea_x - 1)
        fovea_y = (1 - down - up) * fovea_y + \
            down * (fovea_y + 1) + \
            up * (fovea_y - 1)
        fovea_y = tf.clip_by_value(fovea_y, 0, self.shape[0]-1)
        fovea_x = tf.clip_by_value(fovea_x, 0, self.shape[1]-1)
        return fovea_y, fovea_x

    def build_return(
            self, digit, op, acc, fovea_x, fovea_y,
            prev_action, salience, glimpse, actions=None):

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

        if actions is not None:
            _, _, _, _, classify_digit, classify_op, *_ = self.unpack_actions(actions)

            classification_bonus = tf.cond(
                self.is_training_ph,
                lambda: tf.constant(self.classification_bonus, tf.float32),
                lambda: tf.constant(0.0, tf.float32))

            rewards = rewards + classification_bonus * (classify_digit + classify_op)

        return (
            tf.fill((tf.shape(digit)[0], 1), 0.0),
            rewards,
            new_registers)

    def build_step(self, t, r, a):
        _digit, _op, _acc, _fovea_x, _fovea_y, _prev_action, _salience, _glimpse = self.rb.as_tuple(r)

        if self.salience_action:
            (right, left, down, up, classify_digit, classify_op, _,
                update_salience, *arithmetic_actions) = self.unpack_actions(a)

            centres = tf.concat([_fovea_y, _fovea_x], axis=-1) * np.array(self.element_shape)
            centres += np.ceil(np.array(self.element_shape) / 2)
            inp = tf.cast(tf.equal(tf.expand_dims(self.input_ph, -1), -1), tf.float32)
            new_salience = tf.image.extract_glimpse(inp, self.salience_shape, centres, normalized=False, centered=False)
            new_salience = tf.reshape(new_salience, (tf.shape(_digit)[0], -1))

            salience = (1-update_salience) * _salience + update_salience * new_salience
        else:
            right, left, down, up, classify_digit, classify_op, _, *arithmetic_actions = self.unpack_actions(a)
            salience = _salience

        digit = tf.zeros_like(_digit)
        acc = tf.zeros_like(_acc)

        original_factor = tf.ones_like(right)
        for key, action in zip(sorted(self.arithmetic_actions), arithmetic_actions):
            original_factor -= action
            acc += action * self.arithmetic_actions[key](_acc, _digit)
        acc += original_factor * _acc

        acc = tf.clip_by_value(acc, -1000.0, 1000.0)

        digit, op = self.build_update_storage(
            _glimpse, _digit, classify_digit, _op, classify_op)

        fovea_y, fovea_x = self.build_update_fovea(right, left, down, up, _fovea_y, _fovea_x)
        glimpse = self.build_update_glimpse(self.input_ph, fovea_y, fovea_x)

        action = tf.cast(tf.reshape(tf.argmax(a, axis=1), (-1, 1)), tf.float32)

        return self.build_return(
            digit, op, acc, fovea_x, fovea_y, action, salience, glimpse, a)


class GridArithmeticEasy(GridArithmetic):
    def build_step(self, t, r, a):
        _digit, _op, _acc, _fovea_x, _fovea_y, _prev_action, _salience, _glimpse = self.rb.as_tuple(r)

        if self.salience_action:
            (right, left, down, up, classify_digit, classify_op, _,
                update_salience, *arithmetic_actions) = self.unpack_actions(a)

            centres = tf.concat([_fovea_y, _fovea_x], axis=-1) * np.array(self.element_shape)
            centres += np.ceil(np.array(self.element_shape) / 2)
            inp = tf.cast(tf.equal(tf.expand_dims(self.input_ph, -1), -1), tf.float32)
            new_salience = tf.image.extract_glimpse(inp, self.salience_shape, centres, normalized=False, centered=False)
            new_salience = tf.reshape(new_salience, (tf.shape(_digit)[0], -1))

            salience = (1-update_salience) * _salience + update_salience * new_salience
        else:
            right, left, down, up, classify_digit, classify_op, _, *arithmetic_actions = self.unpack_actions(a)
            salience = _salience

        op_classification = self.build_op_classifier(_glimpse)
        op_vision = tf.cast(op_classification, tf.float32)
        op = (1 - classify_op) * _op + classify_op * op_vision

        orig_digit_factor = tf.ones_like(right) - classify_digit
        for action in arithmetic_actions:
            orig_digit_factor -= action

        digit_classification = self.build_digit_classifier(_glimpse)
        digit_vision = tf.cast(digit_classification, tf.float32)

        digit = orig_digit_factor * _digit + (1 - orig_digit_factor) * digit_vision

        orig_acc_factor = tf.ones_like(right)
        acc = tf.zeros_like(_acc)
        for key, action in zip(sorted(self.arithmetic_actions), arithmetic_actions):
            orig_acc_factor -= action
            # Its crucial that we use `digit` here and not `_digit`
            acc += action * self.arithmetic_actions[key](_acc, digit)
        acc += orig_acc_factor * _acc

        acc = tf.clip_by_value(acc, -1000.0, 1000.0)

        fovea_y, fovea_x = self.build_update_fovea(right, left, down, up, _fovea_y, _fovea_x)
        glimpse = self.build_update_glimpse(self.input_ph, fovea_y, fovea_x)

        action = tf.cast(tf.reshape(tf.argmax(a, axis=1), (-1, 1)), tf.float32)

        return self.build_return(
            digit, op, acc, fovea_x, fovea_y, action, salience, glimpse, a)


class GridArithmeticBadWiring(GridArithmetic):
    action_names = [
        '>', '<', 'v', '^', 'classify_digit', 'classify_op',
        '+', '+1', '*', '=', '+ arg', '* arg', '= arg']

    def build_step(self, t, r, a):
        _digit, _op, _acc, _fovea_x, _fovea_y, _glimpse = self.rb.as_tuple(r)

        (right, left, down, up, classify_digit, classify_op,
         add, inc, multiply, store, add_arg, mult_arg, store_arg) = self.unpack_actions(a)

        acc = (1 - add - inc - multiply - store) * _acc + \
            add * (add_arg + _acc) + \
            multiply * (mult_arg * _acc) + \
            inc * (_acc + 1) + \
            store * store_arg

        glimpse = self.build_update_glimpse(self.input_ph, _fovea_y, _fovea_x)

        digit, op = self.build_update_storage(
            glimpse, _digit, classify_digit, _op, classify_op)

        fovea_y, fovea_x = self.build_update_fovea(
            right, left, down, up, _fovea_y, _fovea_x)

        return self.build_return(digit, op, acc, fovea_x, fovea_y, glimpse, a)


class GridArithmeticNoClassifiers(GridArithmetic):
    action_names = ['>', '<', 'v', '^', '+', '+1', '*', '=', '+ arg', '* arg', '= arg']

    def init_classifiers(self):
        return

    def build_step(self, t, r, a):
        _digit, _op, _acc, _fovea_x, _fovea_y, _glimpse = self.rb.as_tuple(r)

        (right, left, down, up, add, inc, multiply, store,
         add_arg, mult_arg, store_arg) = self.unpack_actions(a)

        acc = (1 - add - inc - multiply - store) * _acc + \
            add * (add_arg + _acc) + \
            multiply * (mult_arg * _acc) + \
            inc * (_acc + 1) + \
            store * store_arg

        glimpse = self.build_update_glimpse(self.input_ph, _fovea_y, _fovea_x)

        fovea_y, fovea_x = self.build_update_fovea(
            right, left, down, up, _fovea_y, _fovea_x)

        return self.build_return(_digit, _op, acc, fovea_x, fovea_y, glimpse, a)


class GridArithmeticNoOps(GridArithmetic):
    action_names = ['>', '<', 'v', '^', 'classify_digit', 'classify_op', '=', '= arg']

    def build_step(self, t, r, a):
        _digit, _op, _acc, _fovea_x, _fovea_y, _glimpse = self.rb.as_tuple(r)

        (right, left, down, up, classify_digit, classify_op,
         store, store_arg) = self.unpack_actions(a)

        acc = (1 - store) * _acc + store * store_arg

        glimpse = self.build_update_glimpse(self.input_ph, _fovea_y, _fovea_x)

        digit, op = self.build_update_storage(
            glimpse, _digit, classify_digit, _op, classify_op)

        fovea_y, fovea_x = self.build_update_fovea(
            right, left, down, up, _fovea_y, _fovea_x)

        return self.build_return(digit, op, acc, fovea_x, fovea_y, glimpse, a)


class GridArithmeticNoModules(GridArithmetic):
    action_names = ['>', '<', 'v', '^', '=', '= arg']

    def init_classifiers(self):
        return

    def build_step(self, t, r, a):
        _digit, _op, _acc, _fovea_x, _fovea_y, _glimpse = self.rb.as_tuple(r)
        right, left, down, up, store, store_arg = self.unpack_actions(a)

        acc = (1 - store) * _acc + store * store_arg

        glimpse = self.build_update_glimpse(self.input_ph, _fovea_y, _fovea_x)

        fovea_y, fovea_x = self.build_update_fovea(
            right, left, down, up, _fovea_y, _fovea_x)

        return self.build_return(_digit, _op, acc, fovea_x, fovea_y, glimpse, a)
