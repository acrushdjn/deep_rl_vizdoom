# -*- coding: utf-8 -*-
import tensorflow as tf
import numpy as np
from tensorflow.contrib.framework import arg_scope
from tensorflow.contrib import layers
from .common import _BaseNetwork, gather_2d


class DQNNet(_BaseNetwork):
    def __init__(self,
                 gamma=0.99,
                 double=True,
                 **settings):
        super(DQNNet, self).__init__(**settings)

        self.double = double
        self.gamma = np.float32(gamma)
        self._name_scope = self._get_name_scope()

        self.vars.state2_img = tf.placeholder(tf.float32, self.vars.state_img.shape, name="state2_img")
        if self.use_misc:
            self.vars.state2_misc = tf.placeholder(tf.float32, self.vars.state_misc.shape, name="state2_misc")
        else:
            self.vars.state2_misc = None

        self.vars.a = tf.placeholder(tf.int32, [None], "action")
        self.vars.r = tf.placeholder(tf.float32, [None], "reward")
        self.vars.terminal = tf.placeholder(tf.bool, [None], "terminal")

        global_step = tf.Variable(0, trainable=False, name="global_step")
        if settings["constant_learning_rate"]:
            learning_rate = settings["initial_learning_rate"]
        else:
            learning_rate = tf.train.polynomial_decay(
                learning_rate=settings["initial_learning_rate"],
                end_learning_rate=settings["final_learning_rate"],
                decay_steps=settings["learning_rate_decay_steps"],
                global_step=global_step)

        self.optimizer = tf.train.RMSPropOptimizer(learning_rate=learning_rate, **settings["rmsprop"])
        self._prepare_ops()

    def _prepare_ops(self):
        frozen_name_scope = self._name_scope + "/frozen"

        with arg_scope([layers.conv2d], activation_fn=self.activation_fn, data_format="NCHW"), \
             arg_scope([layers.fully_connected], activation_fn=self.activation_fn):

            q = self.create_architecture(self.vars.state_img, self.vars.state_misc,
                                         name_scope=self._name_scope)
            q2_frozen = self.create_architecture(self.vars.state2_img, self.vars.state2_misc,
                                                 name_scope=frozen_name_scope)
            if self.double:
                q2 = self.create_architecture(self.vars.state2_img, self.vars.state2_misc,
                                              name_scope=self._name_scope, reuse=True)
                best_a2 = tf.argmax(q2, axis=1)
                best_q2 = gather_2d(q2_frozen, best_a2)
            else:
                best_q2 = tf.reduce_max(q2_frozen, axis=1)

        target_q = self.vars.r + (1 - tf.to_float(self.vars.terminal)) * self.gamma * best_q2
        target_q = tf.stop_gradient(target_q)
        tf.stop_gradient(target_q)
        active_q = gather_2d(q, self.vars.a)

        loss = 0.5 * tf.reduce_mean((target_q - active_q) ** 2)

        self.ops.best_action = tf.argmax(q, axis=1)[0]
        self.ops.train_batch = self.optimizer.minimize(loss)

        # Network freezing
        net_params = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope=self._name_scope)
        target_net_params = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope=frozen_name_scope)
        unfreeze_ops = [tf.assign(dst_var, src_var) for src_var, dst_var in zip(net_params, target_net_params)]
        self.ops.unfreeze = tf.group(*unfreeze_ops, name="unfreeze")

    def create_architecture(self, img_input, misc_input, name_scope, reuse=False):
        with arg_scope([layers.conv2d, layers.fully_connected], reuse=reuse), \
             arg_scope([], reuse=reuse):
            fc_input = self.get_input_layers(img_input, misc_input, name_scope)

            fc1 = layers.fully_connected(fc_input,
                                         num_outputs=self.fc_units_num,
                                         scope=name_scope + "/fc1")
            q_op = layers.linear(fc1,
                                 num_outputs=self.actions_num,
                                 scope=name_scope + "/fc_q")

            return q_op

    def get_action(self, sess, state):
        feed_dict = {self.vars.state_img: [state[0]]}
        if self.use_misc:
            feed_dict[self.vars.state_misc] = [state[1]]
        return sess.run(self.ops.best_action, feed_dict=feed_dict)

    def train_batch(self, sess, batch):
        feed_dict = {self.vars.state_img: batch["s1_img"],
                     self.vars.state2_img: batch["s2_img"],
                     self.vars.a: batch["a"],
                     self.vars.r: batch["r"],
                     self.vars.terminal: batch["terminal"]}
        if self.use_misc:
            feed_dict[self.vars.state_misc] = batch["s1_misc"]
            feed_dict[self.vars.state2_misc] = batch["s2_misc"]
        sess.run(self.ops.train_batch, feed_dict=feed_dict)

    def update_target_network(self, session):
        session.run(self.ops.unfreeze)

    def _get_name_scope(self):
        return "dqn"


class DuelingDQNNet(DQNNet):
    def __init__(self, *args, **kwargs):
        super(DuelingDQNNet, self).__init__(*args, **kwargs)

    def _get_name_scope(self):
        return "dueling_dqn"

    def create_architecture(self, img_input, misc_input, name_scope, reuse=False, **specs):
        with arg_scope([layers.conv2d, layers.fully_connected], reuse=reuse), \
             arg_scope([], reuse=reuse):
            fc_input = self.get_input_layers(img_input, misc_input, name_scope)

            fc1 = layers.fully_connected(fc_input, num_outputs=self.fc_units_num, scope=name_scope + "/fc1")

            fc2_value = layers.fully_connected(fc1, num_outputs=256, scope=name_scope + "/fc2_value")
            value = layers.linear(fc2_value, num_outputs=1, scope=name_scope + "/fc3_value")

            fc2_advantage = layers.fully_connected(fc1, num_outputs=256, scope=name_scope + "/fc2_advantage")
            advantage = layers.linear(fc2_advantage, num_outputs=self.actions_num, scope=name_scope + "/fc3_advantage")

            mean_advantage = tf.reshape(tf.reduce_mean(advantage, axis=1), (-1, 1))
            q_op = value + (advantage - mean_advantage)
            return q_op
