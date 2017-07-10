from pprint import pformat
from copy import deepcopy

import tensorflow as tf
from tensorflow.python.ops.rnn_cell_impl import _RNNCell as RNNCell
from tensorflow.python.util.nest import flatten_dict_items
import tensorflow.contrib.distributions as tf_dists

from dps.utils import lst_to_vec, vec_to_lst


def rnn_cell_placeholder(state_size, batch_size=None, dtype=tf.float32, name=''):
    if isinstance(state_size, int):
        return tf.placeholder(dtype, (batch_size, state_size), name=name)
    elif isinstance(state_size, tf.TensorShape):
        return tf.placeholder(dtype, (batch_size,) + tuple(state_size), name=name)
    else:
        ph = [
            rnn_cell_placeholder(
                ss, batch_size=batch_size, dtype=dtype, name="{}/{}".format(name, i))
            for i, ss in enumerate(state_size)]
        return type(state_size)(*ph)


class Policy(RNNCell):
    """ A map from (observation, internal_state) pairs to action activations.

    Each instance of this class can be thought of as owning a set of variables,
    because whenever ``build`` is called, it will try to use the same set of variables
    (i.e. use the same scope, with ``reuse=True``) as the first time it was called.

    Note: in TensorFlow 1.1 (and probably 1.2), its fine to deepcopy an RNNCell up
    until the first time that it is used to add ops to the graph at which point
    variables are created (which are not ommitted by RNNCell.__deepcopy__ in TF 1.2).

    Parameters
    ----------
    controller: callable
        Outputs Tensor giving utilities.
    action_selection: callable
        Outputs Tensor giving action activations.
    exploration: Tensor
        Amount of exploration to use.
    obs_dim: int
        Dimension of observations.
    name: string
        Name of policy, used as name of variable scope.

    """
    def __init__(self, controller, action_selection, exploration, obs_dim, name="policy"):

        self.controller = controller
        self.action_selection = action_selection
        self.exploration = exploration

        self.n_actions = self.action_selection.n_actions
        self.n_params = self.action_selection.n_params
        self.obs_dim = obs_dim
        self.name = name

        self.scope = None
        self.n_builds = 0
        self.act_is_built = False
        self.set_params_op = None
        self.flat_params = None

    def __str__(self):
        return pformat(self)

    def __call__(self, obs, policy_state):
        utils, next_policy_state = self.build_update(obs, policy_state)
        samples = self.build_sample(utils)

        return samples, utils, next_policy_state

    def build_update(self, obs, policy_state):
        with tf.variable_scope(self.get_scope(), reuse=self.n_builds > 0):
            utils, next_policy_state = self.controller(obs, policy_state)

        self.n_builds += 1

        return utils, next_policy_state

    def build_sample(self, utils):
        return self.action_selection.sample(utils, self.exploration)

    def build_log_prob(self, utils, actions):
        return self.action_selection.log_prob(utils, actions, self.exploration)

    def build_entropy(self, utils):
        return self.action_selection.entropy(utils, self.exploration)

    def build_kl(self, utils1, utils2):
        return self.action_selection.kl(utils1, utils2, self.exploration)

    def zero_state(self, batch_size, dtype):
        return self.controller.zero_state(batch_size, dtype)

    @property
    def state_size(self):
        return self.controller.state_size

    @property
    def output_size(self):
        return self.n_actions

    def set_scope(self, scope):
        if self.n_builds > 0:
            raise ValueError("Cannot set scope once Policy has been built for the first time.")
        self.scope = scope

    def capture_scope(self):
        """ Creates a variable scope called self.name inside current active scope, and stores it. """
        with tf.variable_scope(self.name):
            self.set_scope(tf.get_variable_scope())

    def get_scope(self):
        if not self.scope:
            self.capture_scope()
        return self.scope

    def deepcopy(self, new_name):
        if self.n_builds > 0:
            raise ValueError("Cannot copy Policy once it has been built inside a graph.")

        new = Policy(
            deepcopy(self.controller),
            deepcopy(self.action_selection),
            self.exploration, self.obs_dim, new_name)
        return new
        # Should work in TF 1.1, will need to be changed in TF 1.2.
        # new_controller = deepcopy(self.controller)
        # if hasattr(new_controller, '_scope'):
        #     del new_controller._scope

        # new_as = deepcopy(self.action_selection)
        # if hasattr(new_as, '_scope'):
        #     del new_as._scope

        # new = Policy(
        #     deepcopy(self.controller),
        #     deepcopy(self.action_selection),
        #     self.exploration,
        #     self.n_actions, self.obs_dim, new_name)

        # if hasattr(new, '_scope'):
        #     del new._scope

        # return new

    def build_act(self):
        if not self.act_is_built:
            # Build a subgraph that we carry around with the Policy for implementing the ``act`` method
            self._policy_state = rnn_cell_placeholder(self.controller.state_size, name='policy_state')
            self._obs = tf.placeholder(tf.float32, (None, self.obs_dim), name='obs')

            self._utils, self._next_policy_state = self.build_update(self._obs, self._policy_state)
            self._samples = self.build_sample(self._utils)
            self._entropy = self.build_entropy(self._utils)

            self.act_is_built = True

    def build_feeddict(self, obs, policy_state):
        fd = flatten_dict_items({self._policy_state: policy_state})
        fd.update({self._obs: obs})
        return fd

    def act(self, obs, policy_state):
        """ Return (actions, next policy state) given an observation and the current policy state. """
        self.build_act()

        sess = tf.get_default_session()
        feed_dict = self.build_feeddict(obs, policy_state)

        actions, next_policy_state = sess.run([self._samples, self._next_policy_state], feed_dict)

        return actions, next_policy_state

    def build_set_params(self):
        if self.set_params_op is None:
            g = tf.get_default_graph()
            variables = g.get_collection('trainable_variables', scope=self.scope.name)
            self.flat_params_ph = tf.placeholder(tf.float32, lst_to_vec(variables).shape)
            params_lst = vec_to_lst(self.flat_params_ph, variables)

            ops = []
            for p, v in zip(params_lst, variables):
                op = v.assign(p)
                ops.append(op)
            self.set_params_op = tf.group(*ops)

    def set_params_flat(self, flat_params):
        self.build_set_params()
        sess = tf.get_default_session()
        sess.run(self.set_params_op, feed_dict={self.flat_params_ph: flat_params})

    def build_get_params(self):
        if self.flat_params is None:
            g = tf.get_default_graph()
            variables = g.get_collection('trainable_variables', scope=self.scope.name)
            self.flat_params = lst_to_vec(variables)

    def get_params_flat(self):
        self.build_get_params()
        sess = tf.get_default_session()
        flat_params = sess.run(self.flat_params)
        return flat_params


class ActionSelection(object):
    def __init__(self, n_actions):
        self.n_actions = n_actions
        self.n_params = n_actions

    def sample(self, utils, exploration):
        raise Exception()

    def log_prob(self, utils, actions, exploration):
        raise Exception()

    def kl(self, utils1, utils2, exploration):
        raise Exception()


class ProductDist(ActionSelection):
    def __init__(self, *components):
        self.components = components
        self.n_params_vector = [c.n_params for c in components]
        self.n_params = sum(self.n_params_vector)
        self.n_actions_vector = [c.n_actions for c in components]
        self.n_actions = sum(self.n_actions_vector)

    def sample(self, utils, exploration):
        _utils = tf.split(utils, self.n_params_vector, axis=1)
        _samples = [c.sample(u, exploration) for u, c in zip(_utils, self.components)]
        return tf.concat(_samples, axis=1)

    def log_prob(self, utils, actions, exploration):
        _utils = tf.split(utils, self.n_params_vector, axis=1)
        _actions = tf.split(actions, self.n_actions_vector, axis=1)
        _log_probs = [c.log_prob(u, a, exploration) for u, a, c in zip(_utils, _actions, self.components)]
        return tf.reduce_sum(tf.concat(_log_probs, axis=1), axis=1, keep_dims=True)

    def entropy(self, utils, exploration):
        _utils = tf.split(utils, self.n_params_vector, axis=1)
        _entropies = [c.entropy(u, exploration) for u, c in zip(_utils, self.components)]
        return tf.reduce_sum(tf.concat(_entropies, axis=1), axis=1, keep_dims=True)

    def kl(self, utils1, utils2, e1, e2=None):
        _utils1 = tf.split(utils1, self.n_params_vector, axis=1)
        _utils2 = tf.split(utils2, self.n_params_vector, axis=1)

        _splitwise_kl = tf.concat(
            [c.kl(u1, u2, e1, e2)
             for u1, u2, c in zip(_utils1, _utils2, self.components)],
            axis=1)

        return tf.reduce_sum(_splitwise_kl, axis=1, keep_dims=True)


class TensorFlowSelection(ActionSelection):
    def _dist(self, utils, exploration):
        raise Exception()

    def sample(self, utils, exploration):
        dist = self._dist(utils, exploration)
        samples = tf.cast(dist.sample(), tf.float32)
        return tf.reshape(samples, (-1, self.n_actions))

    def log_prob(self, utils, actions, exploration):
        dist = self._dist(utils, exploration)
        actions = tf.reshape(actions, tf.shape(dist.sample()))
        return tf.reshape(dist.log_prob(actions), (-1, 1))

    def entropy(self, utils, exploration):
        dist = self._dist(utils, exploration)
        return tf.reshape(dist.entropy(), (-1, 1))

    def kl(self, utils1, utils2, e1, e2=None):
        e2 = e1 if e2 is None else e2
        dist1 = self._dist(utils1, e1)
        dist2 = self._dist(utils2, e2)
        return tf.reshape(tf_dists.kl(dist1, dist2), (-1, 1))


def softplus(x):
    return tf.log(1 + tf.exp(x))


class Normal(TensorFlowSelection):
    def __init__(self):
        self.n_actions = 1
        self.n_params = 2

    def _dist(self, utils, exploration):
        mean = utils[:, 0]
        scale = softplus(utils[:, 1])
        # Could use tf_dists.NormalWithSoftplusScale, but found it to cause problems
        # when taking hessian-vector products.
        dist = tf_dists.Normal(loc=mean, scale=scale)
        return dist


class NormalWithFixedScale(TensorFlowSelection):
    def __init__(self, scale):
        self.n_actions = 1
        self.n_params = 1
        self.scale = scale

    def _dist(self, utils, exploration):
        dist = tf_dists.Normal(loc=utils[:, 0], scale=self.scale)
        return dist


class Gamma(TensorFlowSelection):
    """ alpha, beta """
    def __init__(self):
        self.n_actions = 1
        self.n_params = 2

    def _dist(self, utils, exploration):
        concentration = softplus(utils[:, 0])
        rate = softplus(utils[:, 1])

        dist = tf_dists.Gamma(concentration=concentration, rate=rate)
        return dist


class Bernoulli(TensorFlowSelection):
    def __init__(self):
        self.n_actions = self.n_params = 1

    def _dist(self, utils, exploration):
        return tf_dists.BernoulliWithSigmoidProbs(utils[:, 0])


class Softmax(TensorFlowSelection):
    def __init__(self, n_actions, one_hot=True):
        self.n_params = n_actions
        self.n_actions = n_actions if one_hot else 1
        self.one_hot = one_hot

    def _dist(self, utils, exploration):
        logits = utils / exploration

        if self.one_hot:
            return tf_dists.OneHotCategorical(logits=logits)
        else:
            return tf_dists.Categorical(logits=logits)


class EpsilonGreedy(TensorFlowSelection):
    def __init__(self, n_actions, one_hot=True):
        self.n_params = n_actions
        self.n_actions = n_actions if one_hot else 1
        self.one_hot = one_hot

    def _probs(self, q_values, exploration):
        epsilon = exploration
        mx = tf.reduce_max(q_values, axis=1, keep_dims=True)
        bool_is_max = tf.equal(q_values, mx)
        float_is_max = tf.cast(bool_is_max, tf.float32)
        max_count = tf.reduce_sum(float_is_max, axis=1, keep_dims=True)
        _probs = (float_is_max / max_count) * (1 - epsilon)
        n_actions = tf.shape(q_values)[1]
        probs = _probs + epsilon / tf.cast(n_actions, tf.float32)
        return probs

    def _dist(self, q_values, exploration):
        probs = self._probs(q_values, exploration)
        if self.one_hot:
            return tf_dists.OneHotCategorical(probs=probs)
        else:
            return tf_dists.Categorical(probs=probs)


class Deterministic(TensorFlowSelection):
    def __init__(self, n_params, n_actions=None, func=None):
        self.n_params = n_params
        self.n_actions = n_actions or n_params
        self.func = func or (lambda x: tf.identity(x))

    def _dist(self, utils, exploration):
        return tf_dists.VectorDeterministic(self.func(utils))

    def entropy(self, utils, exploration):
        return tf.fill((tf.shape(utils)[0], 1), 0.)

    def kl(self, utils1, utils2, e1, e2=None):
        raise Exception()
