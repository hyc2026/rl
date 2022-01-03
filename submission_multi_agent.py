
from typing import Dict, Tuple, Any
import copy
import base64
import pickle
import random
import numpy as np
import torch
import torch.nn as nn

from zerosum_env.envs.carbon.helpers import RecrtCenterAction, WorkerAction, Board


ModelBaseActions = [None,
               RecrtCenterAction.RECCOLLECTOR,
               RecrtCenterAction.RECPLANTER]

ModelWorkerActions = [None,
                 WorkerAction.UP,
                 WorkerAction.RIGHT,
                 WorkerAction.DOWN,
                 WorkerAction.LEFT]

ModelWorkerDirections = np.stack([np.array((0, 0)),
                             np.array((0, 1)),
                             np.array((1, 0)),
                             np.array((0, -1)),
                             np.array((-1, 0))])  # 与WorkerActions相对应


ModelBaseActionsByName = {action.name: action for action in ModelBaseActions if action is not None}

ModelWorkerActionsByName = {action.name: action for action in ModelWorkerActions if action is not None}


def model_init_(module, gain=1):
    nn.init.orthogonal_(module.weight.data, gain=gain)
    nn.init.constant_(module.bias.data, 0.)
    return module


def model_one_hot_np(value: int, num_cls: int):
    ret = np.zeros(num_cls)
    ret[value] = 1
    return ret


def model_to_tensor(value: Any, raise_error=True) -> torch.Tensor:
    if torch.is_tensor(value):
        return value
    elif isinstance(value, np.ndarray):
        return torch.from_numpy(value)
    elif isinstance(value, (tuple, list)):
        if torch.is_tensor(value[0]):
            return torch.stack(value)
        elif isinstance(value[0], np.ndarray):
            return torch.tensor(value)
        else:
            try:
                return torch.tensor(value)
            except Exception as ex:
                pass
    else:
        pass
    if raise_error:
        raise TypeError("not support item type: {}".format(type(value)))
    return None


class Model(nn.Module):
    def __init__(self, is_actor=True):
        super().__init__()
        self.dense_dim = 8
        self.action_dim = 5

        gain = nn.init.calculate_gain('leaky_relu', 0.01)

        self.backbone = nn.Sequential(
            model_init_(nn.Conv2d(13, 64, kernel_size=(3, 3), stride=(1, 1)), gain=gain),
            nn.LeakyReLU(negative_slope=0.01),
            model_init_(nn.Conv2d(64, 64, kernel_size=(3, 3), stride=(1, 1), groups=64), gain=gain),
            nn.LeakyReLU(negative_slope=0.01),
            model_init_(nn.Conv2d(64, 64, kernel_size=(2, 2), stride=(2, 2)), gain=gain),
            nn.LeakyReLU(negative_slope=0.01),
            nn.Flatten(start_dim=1, end_dim=-1),
        )

        self.header = nn.Sequential(
            nn.LayerNorm(1600 + self.dense_dim),
            model_init_(nn.Linear(1600 + self.dense_dim, 256), gain=gain),
            nn.LeakyReLU(),
            nn.LayerNorm(256),
            model_init_(nn.Linear(256, 128), gain=gain),
            nn.LeakyReLU(negative_slope=0.01),
            nn.LayerNorm(128),
        )
        if is_actor:
            self.out = model_init_(nn.Linear(128, self.action_dim))
        else:
            self.out = model_init_(nn.Linear(128, 1))

    def forward(self, x):
        dense = x[:, :self.dense_dim]
        state = x[:, self.dense_dim:].reshape((-1, 13, 15, 15))
        x = self.backbone(state)
        x = torch.hstack([x, dense])

        x = self.header(x)
        output = self.out(x)
        return output


class ModelObservationParser:
    """
    ObservationParser class is used to parse observation dict data and converted to observation tensor for training.

    The features are included as follows:

    """
    def __init__(self, grid_size=15,
                 max_step=300,
                 max_cell_carbon=100,
                 tree_lifespan=50,
                 action_space=5):
        self.grid_size = grid_size
        self.max_step = max_step
        self.max_cell_carbon = max_cell_carbon
        self.tree_lifespan = tree_lifespan
        self.action_space = action_space

    @property
    def observation_cnn_shape(self) -> Tuple[int, int, int]:
        """
        状态空间中CNN特征的维度
        :return: CNN状态特征的维度
        """
        return 13, 15, 15

    @property
    def observation_vector_shape(self) -> int:
        """
        状态空间中一维向量的维度
        :return: 状态空间中一维向量的维度
        """
        return 8

    @property
    def observation_dim(self) -> int:
        """
        状态空间的总维度(cnn维度 + vector维度). CNN特征会被展平成一维, 跟vector特征合并返回.
        :return: 状态空间的总维度
        """
        return 8 + 13 * 15 * 15

    def _guess_previous_actions(self, previous_obs: Board, current_obs: Board) -> Dict:
        """
        基于连续两帧Board信息,猜测各个agent采用的动作(已经消失的agent,因无法准确估计,故忽略!)

        :return:  字典, key为agent_id, value为Command或None
        """
        return_value = {}

        previous_workers = previous_obs.workers if previous_obs is not None else {}
        current_workers = current_obs.workers if current_obs is not None else {}

        player_base_cmds = {player_id: ModelBaseActions[0]
                            for player_id in current_obs.players.keys()} if current_obs is not None else {}
        total_worker_ids = set(previous_workers.keys()) | set(current_workers.keys())  # worker id列表
        for worker_id in total_worker_ids:
            previous_worker, worker = previous_workers.get(worker_id, None), current_workers.get(worker_id, None)
            if previous_worker is not None and worker is not None:  # (连续两局存活) 移动/停留 动作
                prev_pos = np.array([previous_worker.position.x, previous_worker.position.y])
                curr_pos = np.array([worker.position.x, worker.position.y])

                # 计算所有方向的可能位置 (防止越界问题)
                next_all_positions = ((prev_pos + ModelWorkerDirections) + self.grid_size) % self.grid_size
                dir_index = (next_all_positions == curr_pos).all(axis=1).nonzero()[0].item()
                cmd = ModelWorkerActions[dir_index]

                return_value[worker_id] = cmd
            elif previous_worker is None and worker is not None:  # (首次出现) 招募 动作
                if worker.is_collector:
                    player_base_cmds[worker.player_id] = ModelBaseActions[1]
                elif worker.is_planter:
                    player_base_cmds[worker.player_id] = ModelBaseActions[2]
            else:  # Agent已消失(因无法准确推断出动作), 忽略
                pass

        if current_obs is not None:  # 转换中心指令
            for player_id, player in current_obs.players.items():
                return_value[player.recrtCenter_ids[0]] = player_base_cmds[player_id]

        return return_value

    def _distance_feature(self, x, y) -> np.ndarray:
        """
        Calculate the minimum distance from current position to other positions on grid.
        :param x: position x
        :param y: position y
        :return distance_map: 2d-array, the value in the grid indicates the minimum distance form position (x, y) to
            current position.
        """
        distance_y = (np.ones((self.grid_size, self.grid_size)) * np.arange(self.grid_size)).astype(np.float32)
        distance_x = distance_y.T
        delta_distance_x = abs(distance_x - x)
        delta_distance_y = abs(distance_y - y)
        offset_distance_x = self.grid_size - delta_distance_x
        offset_distance_y = self.grid_size - delta_distance_y
        distance_x = np.where(delta_distance_x < offset_distance_x,
                              delta_distance_x, offset_distance_x)
        distance_y = np.where(delta_distance_y < offset_distance_y,
                              delta_distance_y, offset_distance_y)
        distance_map = distance_x + distance_y

        return distance_map

    def obs_transform(self, current_obs: Board, previous_obs: Board = None) -> Tuple[Dict, Dict, Dict]:
        """
        通过前后两帧的原始观测状态值, 计算状态空间特征, agent dones信息以及agent可用的动作空间.

        特征维度包含:
            1) vector_feature：(一维特征, dim: 8)
                Step feature: range [0, 1], dim 1, 游戏轮次
                my_cash: range [-1, 1], dim 1, 玩家金额
                opponent_cash: range [-1, 1], dim 1, 对手金额
                agent_type: range [0, 1], dim 3, agent类型(one-hot)
                x: range [0, 1), dim 1, agent x轴位置坐标
                y: range [0, 1), dim 1, agent y轴位置坐标
            2) cnn_feature: (CNN特征, dim: 13x15x15)
                carbon_feature: range [0, 1], dim: 1x15x15, 地图碳含量分布
                base_feature: range [-1, 1], dim: 1x15x15, 转化中心位置分布(我方:+1, 对手:-1)
                collector_feature: range [-1, 1], dim: 1x15x15, 捕碳员位置分布(我方:+1, 对手:-1)
                planter_feature: range [-1, 1], dim: 1x15x15, 种树员位置分布(我方:+1, 对手:-1)
                worker_carbon_feature: range [-1, 1], dim: 1x15x15, 捕碳员携带CO2量分布(我方:>=0, 对手:<=0)
                tree_feature: [-1, 1], dim: 1x15x15, 树分布,绝对值表示树龄(我方:>=0, 对手:<=0)
                action_feature:[0, 1], dim: 5x15x15, 上一轮次动作分布(one-hot)
                my_base_distance_feature: [0, 1], dim: 1x15x15, 我方转化中心在地图上与各点位的最短距离分布
                distance_features: [0, 1], dim: 1x15x15, 当前agent距离地图上各点位的最短距离分布

        :param current_obs: 当前轮次原始的状态
        :param previous_obs: 前一轮次原始的状态 (default: None)
        :return local_obs: (Dict[str, np.ndarray]) 当前选手每个agent的observation特征 (vector特征+CNN特征展成的一维特征)
        :return dones: (Dict[str, bool]) 当前选手每个agent的done标识, True-agent已死亡, False-agent尚存活
        :return available_actions: (Dict[str, np.ndarray]) 标识当前选手每个agent的动作维度是否可用, 1表示该动作可用,
            0表示动作不可用
        """
        # 加入agent上一轮次的动作
        agent_cmds = self._guess_previous_actions(previous_obs, current_obs)
        previous_action = {k: v.value if v is not None else 0 for k, v in agent_cmds.items()}

        available_actions = {}
        my_player_id = current_obs.current_player_id

        carbon_feature = np.zeros((self.grid_size, self.grid_size), dtype=np.float32)
        for point, cell in current_obs.cells.items():
            if cell.carbon > 0:
                carbon_feature[point.x, point.y] = cell.carbon / self.max_cell_carbon

        step_feature = current_obs.step / (self.max_step - 1)
        base_feature = np.zeros_like(carbon_feature, dtype=np.float32)  # me: +1; opponent: -1
        collector_feature = np.zeros_like(carbon_feature, dtype=np.float32)  # me: +1; opponent: -1
        planter_feature = np.zeros_like(carbon_feature, dtype=np.float32)  # me: +1; opponent: -1
        worker_carbon_feature = np.zeros_like(carbon_feature, dtype=np.float32)
        tree_feature = np.zeros_like(carbon_feature, dtype=np.float32)  # trees, me: +; opponent: -.
        action_feature = np.zeros((self.grid_size, self.grid_size, self.action_space), dtype=np.float32)

        my_base_distance_feature = None
        distance_features = {}

        my_cash, opponent_cash = current_obs.current_player.cash, current_obs.opponents[0].cash
        for base_id, base in current_obs.recrtCenters.items():
            is_myself = base.player_id == my_player_id

            base_x, base_y = base.position.x, base.position.y

            base_feature[base_x, base_y] = 1.0 if is_myself else -1.0
            base_distance_feature = self._distance_feature(base_x, base_y) / (self.grid_size - 1)
            distance_features[base_id] = base_distance_feature

            action_feature[base_x, base_y] = model_one_hot_np(previous_action.get(base_id, 0), self.action_space)
            if is_myself:
                available_actions[base_id] = np.array([1, 1, 1, 0, 0])  #

                my_base_distance_feature = distance_features[base_id]

        for worker_id, worker in current_obs.workers.items():
            is_myself = worker.player_id == my_player_id

            available_actions[worker_id] = np.array([1, 1, 1, 1, 1])  #

            worker_x, worker_y = worker.position.x, worker.position.y
            distance_features[worker_id] = self._distance_feature(worker_x, worker_y) / (self.grid_size - 1)

            action_feature[worker_x, worker_y] = model_one_hot_np(previous_action.get(worker_id, 0), self.action_space)

            if worker.is_collector:
                collector_feature[worker_x, worker_y] = 1.0 if is_myself else -1.0
            else:
                planter_feature[worker_x, worker_y] = 1.0 if is_myself else -1.0

            worker_carbon_feature[worker_x, worker_y] = worker.carbon
        worker_carbon_feature = np.clip(worker_carbon_feature / self.max_cell_carbon / 2, -1, 1)

        for tree in current_obs.trees.values():
            tree_feature[tree.position.x, tree.position.y] = tree.age if tree.player_id == my_player_id else -tree.age
        tree_feature /= self.tree_lifespan

        global_vector_feature = np.stack([step_feature,
                                          np.clip(my_cash / 2000., -1., 1.),
                                          np.clip(opponent_cash / 2000., -1., 1.),
                                          ]).astype(np.float32)
        global_cnn_feature = np.stack([carbon_feature,
                                       base_feature,
                                       collector_feature,
                                       planter_feature,
                                       worker_carbon_feature,
                                       tree_feature,
                                       *action_feature.transpose(2, 0, 1),  # dim: 5 x 15 x 15
                                       ])  # dim: 11 x 15 x 15

        dones = {}
        local_obs = {}
        previous_worker_ids = set() if previous_obs is None else set(previous_obs.current_player.worker_ids)
        worker_ids = set(current_obs.current_player.worker_ids)
        new_worker_ids, death_worker_ids = worker_ids - previous_worker_ids, previous_worker_ids - worker_ids
        obs = previous_obs if previous_obs is not None else current_obs
        total_agents = obs.current_player.recrtCenters + \
                       obs.current_player.workers + \
                       [current_obs.workers[id_] for id_ in new_worker_ids]  # 基地 + prev_workers + new_workers
        for my_agent in total_agents:
            if my_agent.id in death_worker_ids:  # 死亡的agent, 直接赋值为0
                local_obs[my_agent.id] = np.zeros(self.observation_dim, dtype=np.float32)
                available_actions[my_agent.id] = np.array([1, 1, 1, 1, 1])  #
                dones[my_agent.id] = True
            else:  # 未死亡的agent
                cnn_feature = np.stack([*global_cnn_feature,
                                        my_base_distance_feature,
                                        distance_features[my_agent.id],
                                        ])  # dim: 2925 (13 x 15 x 15)
                if not hasattr(my_agent, 'is_collector'):  # 转化中心
                    agent_type = [1, 0, 0]
                else:  # 工人
                    agent_type = [0, int(my_agent.is_collector), int(my_agent.is_planter)]
                vector_feature = np.stack([*global_vector_feature,
                                           *agent_type,
                                           my_agent.position.x / self.grid_size,
                                           my_agent.position.y / self.grid_size,
                                           ]).astype(np.float32)  # dim: 8
                local_obs[my_agent.id] = np.concatenate([vector_feature, cnn_feature.reshape(-1)])
                dones[my_agent.id] = False

        return local_obs, dones, available_actions


class ModelPolicy:
    def __init__(self):
        self.obs_parser = ModelObservationParser()
        self.actor_model = Model(is_actor=True)

    def take_action(self, observation, configuration):
        current_obs = Board(observation, configuration)
        previous_obs = self.previous_obs if current_obs.step > 0 else None

        agent_obs_dict, dones, available_actions_dict = self.obs_parser.obs_transform(current_obs, previous_obs)
        self.previous_obs = copy.deepcopy(current_obs)

        agent_ids, agent_obs, avail_actions = zip(*[(agent_id, torch.from_numpy(obs_), available_actions_dict[agent_id])
                                                    for agent_id, obs_ in agent_obs_dict.items()])
        agent_obs = model_to_tensor(agent_obs)
        avail_actions = model_to_tensor(avail_actions)
        action_logits = self.actor_model(agent_obs)
        action_logits[avail_actions == 0] = torch.finfo(torch.float32).min

        # 按照概率值倒排,选择最大概率位置的索引
        actions = action_logits.sort(dim=1, descending=True)[1][:, 0].detach().cpu().numpy().flatten()

        env_commands = {}
        for agent_id, action_value in zip(agent_ids, actions):
            actions = ModelBaseActions if 'recrtCenter' in agent_id else ModelWorkerActions
            if 0 < action_value < len(actions):
                env_commands[agent_id] = actions[action_value].name
        return env_commands



model = base64.b64decode(model)
model = pickle.loads(model)
for name, param in model.items():
    model[name] = torch.tensor(param)


model_policy = ModelPolicy()
model_policy.actor_model.load_state_dict(model)


def model_agent(obs, configuration):
    global model_policy
    commands = model_policy.take_action(obs, configuration)
    return commands


import copy
import numpy as np

import random
from abc import abstractmethod

# from envs.obs_parser import ObservationParser
from zerosum_env.envs.carbon.helpers import (Board, Cell, Collector, Planter,
                                             Point, RecrtCenter,
                                             RecrtCenterAction, WorkerAction)

from typing import Tuple, Dict, List


# TODO: 大问题： 任务基地闪烁


BaseActions = [None,
               RecrtCenterAction.RECCOLLECTOR,
               RecrtCenterAction.RECPLANTER]

WorkerActions = [None,
                 WorkerAction.UP,
                 WorkerAction.RIGHT,
                 WorkerAction.DOWN,
                 WorkerAction.LEFT]

TOP_CARBON_CONTAIN = 5


class BasePolicy:
    """
    Base policy class that wraps actor and critic models to calculate actions and value for training and evaluating.
    """
    def __init__(self):
        pass

    def policy_reset(self, episode: int, n_episodes: int):
        """
        Policy Reset at the beginning of the new episode.
        :param episode: (int) current episode
        :param n_episodes: (int) number of total episodes
        """
        pass

    def can_sample_trajectory(self) -> bool:
        """
        Specifies whether the policy's actions output and values output can be collected for training or not
            (default False).
        :return: True means the policy's trajectory data can be collected for training, otherwise False.
        """
        return False

    def get_actions(self, observation, available_actions=None) -> Tuple[np.ndarray, np.ndarray]:
        """
        Compute actions predictions for the given inputs.
        :param observation:  (np.ndarray) local agent inputs to the actor.
        :param available_actions: (np.ndarray) denotes which actions are available to agent
                                  (if None, all actions available)

        :return actions: (np.ndarray) actions to take.
        :return action_log_probs: (np.ndarray) log probabilities of chosen actions.
        """
        raise NotImplementedError("not implemented")

    def state_dict(self):
        """
        Returns a whole state of models and optimizers.
        :return:
            dict:
                a dictionary containing a whole state of the module
        """
        pass

    def restore(self, model_dict, strict=True):
        """
        Restore models and optimizers from model_dict.

        :param model_dict: (dict) State dict of models and optimizers.
        :param strict: (bool, optional) whether to strictly enforce that the keys
        """
        pass

    @staticmethod
    def to_env_commands(policy_actions: Dict[str, int]) -> Dict[str, str]:
        """
        Actions output from policy convert to actions environment can accept.
        :param policy_actions: (Dict[str, int]) Policy actions which specifies the agent name and action value.
        :return env_commands: (Dict[str, str]) Commands environment can accept,
            which specifies the agent name and the command string (None is the stay command, no need to send!).
        """

        def agent_action(agent_name, command_value) -> str:
            # hack here, 判断是否为转化中心,然后返回各自的命令
            actions = BaseActions if 'recrtCenter' in agent_name else WorkerActions
            return actions[command_value].name if 0 < command_value < len(actions) else None

        env_commands = {}
        for agent_id, cmd_value in policy_actions.items():
            command = agent_action(agent_id, cmd_value)
            if command is not None:
                env_commands[agent_id] = command
        return env_commands


class AgentBase:
    def __init__(self):
        pass

    def move(self, **kwargs):
        """移动行为，需要移动到什么位置"""
        pass

    @ abstractmethod
    def action(self, **kwargs):
        pass


def calculate_carbon_contain(map_carbon_cell: Dict) -> Dict:
    """遍历地图上每一个位置，附近碳最多的位置按从多到少进行排序"""
    carbon_contain_dict = dict()  # 用来存储地图上每一个位置周围4个位置当前的碳含量, {(0, 0): 32}
    for _loc, cell in map_carbon_cell.items():

        valid_loc = [(_loc[0], _loc[1] - 1),
                     (_loc[0] - 1, _loc[1]),
                     (_loc[0] + 1, _loc[1]),
                     (_loc[0], _loc[1] + 1)]  # 四个位置，按行遍历时从小到大

        forced_pos_valid_loc = str(valid_loc).replace('-1', '14')  # 因为棋盘大小是 15 * 15
        forced_pos_valid_loc = eval(forced_pos_valid_loc.replace('15', '0'))

        filter_cell = \
            [_c for _, _c in map_carbon_cell.items() if getattr(_c, "position", (-100, -100)) in forced_pos_valid_loc]

        assert len(filter_cell) == 4  # 因为选取周围四个值来吸收碳

        carbon_contain_dict[cell] = sum([_fc.carbon for _fc in filter_cell])

    map_carbon_sum_sorted = dict(sorted(carbon_contain_dict.items(), key=lambda x: x[1], reverse=True))

    return map_carbon_sum_sorted

def cal_carbon_one_cell(cell, map_carbon_cell: Dict) -> float:
    _loc = cell.position
    valid_loc = [(_loc[0], _loc[1] - 1),
                 (_loc[0] - 1, _loc[1]),
                 (_loc[0] + 1, _loc[1]),
                 (_loc[0], _loc[1] + 1)]  # 四个位置，按行遍历时从小到大

    forced_pos_valid_loc = str(valid_loc).replace('-1', '14')  # 因为棋盘大小是 15 * 15
    forced_pos_valid_loc = eval(forced_pos_valid_loc.replace('15', '0'))

    filter_cell = \
        [_c for _, _c in map_carbon_cell.items() if getattr(_c, "position", (-100, -100)) in forced_pos_valid_loc]

    assert len(filter_cell) == 4  # 因为选取周围四个值来吸收碳

    return sum([_fc.carbon for _fc in filter_cell])

def calculate_carbon_contain_single(map_carbon_cell: Dict) -> Dict:
    """遍历地图上每一个位置，当前碳最多的位置按从多到少进行排序"""
    carbon_contain_dict = dict()  # 用来存储地图上每一个位置周围4个位置当前的碳含量, {(0, 0): 32}
    for _loc, cell in map_carbon_cell.items():

        valid_loc = [(_loc[0], _loc[1])]  # 四个位置，按行遍历时从小到大

        forced_pos_valid_loc = str(valid_loc).replace('-1', '14')  # 因为棋盘大小是 15 * 15
        forced_pos_valid_loc = eval(forced_pos_valid_loc.replace('15', '0'))

        filter_cell = \
            [_c for _, _c in map_carbon_cell.items() if getattr(_c, "position", (-100, -100)) in forced_pos_valid_loc]

        assert len(filter_cell) == 1  # 因为选取周围四个值来吸收碳

        carbon_contain_dict[cell] = sum([_fc.carbon for _fc in filter_cell])

    map_carbon_sum_sorted = dict(sorted(carbon_contain_dict.items(), key=lambda x: x[1], reverse=True))

    return map_carbon_sum_sorted



class CollectorAct(AgentBase):
    def __init__(self):
        super().__init__()
        self.action_dict = {}
        for move in WorkerAction.moves():
            self.action_dict[move.name] = move
        self.collector_target = dict()

    @staticmethod
    def _minimum_distance(point_1, point_2):
        abs_distance = abs(point_1 - point_2)
        cross_distance = min(point_1, point_2) + (15 - max(point_1, point_2))  # TODO: 这里对吗，是14减?
        return min(abs_distance, cross_distance)

    def _calculate_distance(self, collector_position, current_position):
        """计算真实距离，计算跨图距离，取两者最小值"""
        x_distance = self._minimum_distance(collector_position[0], current_position[0])
        y_distance = self._minimum_distance(collector_position[1], current_position[1])

        return x_distance + y_distance

    def _target_plan(self, collector: Collector, carbon_sort_dict: Dict, ours_info, oppo_info):
        """结合某一位置的碳的含量和距离"""
        # TODO：钱够不够是否考虑？
        global overall_plan
        collector_position = collector.position
        # 取碳排量最高的前十
        carbon_sort_dict_top_n = \
            {_v: _k for _i, (_v, _k) in enumerate(carbon_sort_dict.items()) if
             _i < 225}  # 只选取含碳量top_n的cell来进行计算，拿全部的cell可能会比较耗时？
        # 计算planter和他的相对距离，并且结合该位置四周碳的含量，得到一个总的得分
        planned_target = [Point(*_v.position) for _k, _v in self.collector_target.items()]
        max_score, max_score_cell = -1e9, None
        for _cell, _carbon_sum in carbon_sort_dict_top_n.items():
            #if (_cell.position not in planned_target):  # 这个位置不在其他智能体正在进行的plan中
            if (_cell.position not in planned_target):  # 这个位置不在其他智能体正在进行的plan中
                collector_to_cell_distance = self._calculate_distance(collector_position, _cell.position)  # 我们希望这个距离越小越好
                cell_to_center_distance = self._calculate_distance(_cell.position, ours_info.recrtCenters[0].position)
                collector_to_center_distance = self._calculate_distance(collector_position, ours_info.recrtCenters[0].position)

                #if collector_to_center_distance!=collector_to_cell_distance+cell_to_center_distance:
                collector_to_center_distance += (collector_to_cell_distance+cell_to_center_distance-collector_to_center_distance) /3
                target_preference_score = min(_carbon_sum*(1.03**collector_to_cell_distance)/(collector_to_cell_distance+1), 200)

                #_carbon_sum + collector_to_cell_distance * (-7)  # 不考虑碳总量只考虑距离 TODO: 这会导致中了很多树，导致后期花费很高

                if target_preference_score > max_score:
                    max_score = target_preference_score
                    max_score_cell = _cell

        if max_score_cell is None:  # 没有找到符合条件的最大得分的cell，随机选一个cell
            max_score_cell = random.choice(list(carbon_sort_dict_top_n))

        if self.rct_attacker_id == collector.id and \
            self._calculate_distance(collector_position, oppo_info[0].recrtCenters[0].position)>0:
                max_score_cell = oppo_info[0].recrtCenters[0].cell

        return max_score_cell

    def _target_plan_2_home(self, collector: Collector, carbon_sort_dict: Dict, ours_info):
        """结合某一位置的碳的含量和距离"""
        # TODO：钱够不够是否考虑？
        global overall_plan
        collector_position = collector.position
        # 取碳排量最高的前十
        carbon_sort_dict_top_n = \
            {_v: _k for _i, (_v, _k) in enumerate(carbon_sort_dict.items()) if
             _i < 225}  # 只选取含碳量top_n的cell来进行计算，拿全部的cell可能会比较耗时？
        # 计算planter和他的相对距离，并且结合该位置四周碳的含量，得到一个总的得分
        planned_target = [Point(*_v.position) for _k, _v in self.collector_target.items()]
        max_score, max_score_cell = -1e9, None

        dis0 = self._calculate_distance(ours_info.recrtCenters[0].cell.position, collector.position)
        dis1 = self._calculate_distance(ours_info.recrtCenters[1].cell.position, collector.position)
        if dis0<dis1:
            r_id = 0
        else:
            r_id = 1

        max_score_cell = ours_info.recrtCenters[r_id].cell
        for _cell, _carbon_sum in carbon_sort_dict_top_n.items():
            #if (_cell.position not in planned_target):  # 这个位置不在其他智能体正在进行的plan中
            if (_cell.position not in planned_target):  # 这个位置不在其他智能体正在进行的plan中
                collector_to_cell_distance = self._calculate_distance(collector_position, _cell.position)  # 我们希望这个距离越小越好
                cell_to_center_distance = self._calculate_distance(_cell.position, ours_info.recrtCenters[r_id].position)
                collector_to_center_distance = self._calculate_distance(collector_position, ours_info.recrtCenters[r_id].position)

                if collector_to_center_distance!=collector_to_cell_distance+cell_to_center_distance:
                    continue
                if _carbon_sum*(1.03**collector_to_cell_distance)<15:
                    continue
                collector_to_center_distance += (collector_to_cell_distance+cell_to_center_distance-collector_to_center_distance) /3
                target_preference_score = min(_carbon_sum*(1.03**collector_to_cell_distance)/(0.3*collector_to_cell_distance+1), 200)

                if target_preference_score > max_score:
                    max_score = target_preference_score
                    max_score_cell = _cell

        if max_score_cell is None:  # 没有找到符合条件的最大得分的cell，随机选一个cell
            max_score_cell = random.choice(list(carbon_sort_dict_top_n))

        return max_score_cell

    def _target_plan_attacker(self, collector: Collector, ours_info, oppo_info):
        global overall_plan

        max_score, max_score_cell = -1e9, None

        max_score_cell = oppo_info[0].recrtCenters[0].cell
        collector_position = collector.position
        for oppo_collector in oppo_info[0].collectors:
            if oppo_collector.carbon <= collector.carbon:
                continue
            _cell = oppo_collector.cell
            collector_to_cell_distance = self._calculate_distance(collector_position, _cell.position)

            target_preference_score = -collector_to_cell_distance + oppo_collector.carbon/30

            if target_preference_score > max_score:
                max_score = target_preference_score
                max_score_cell = _cell

        for oppo_planter in oppo_info[0].planters:
            _cell = oppo_planter.cell
            collector_to_cell_distance = self._calculate_distance(collector_position, _cell.position)

            target_preference_score = -collector_to_cell_distance

            if target_preference_score > max_score:
                max_score = target_preference_score
                max_score_cell = _cell

        if self.rct_attacker_id == collector.id:
            dis0 = self._calculate_distance(collector_position, oppo_info[0].recrtCenters[0].position)
            dis1 = self._calculate_distance(collector_position, oppo_info[0].recrtCenters[1].position)
            if dis0<dis1:
                r_id = 0
            else:
                r_id = 1
            if min(dis0, dis1)>1:
                max_score_cell = oppo_info[0].recrtCenters[r_id].cell

        return max_score_cell

    def cal_cell_tree_num(self, cell, ours_info):
        check_cell_list = [cell.left, cell.right, cell.down, cell.up, cell.up.right, cell.up.left,
                           cell.down.left, cell.down.right]
        return sum([False if (_c.tree is None or _c.tree.player_id==ours_info.id) else True for _c in check_cell_list])

    def _check_surround_validity(self, move: WorkerAction, collector: Collector, steps) -> bool:
        move = move.name

        if move == 'UP':
            # 需要看前方三个位置有没有Agent
            check_cell_list = [collector.cell.up]
        elif move == 'DOWN':
            check_cell_list = [collector.cell.down]
        elif move == 'RIGHT':
            check_cell_list = [collector.cell.right]
        elif move == 'LEFT':
            check_cell_list = [collector.cell.left]
        else:
            raise NotImplementedError


        if move == 'UP':
            # 需要看前方三个位置有没有Agent
            check_cell_list_2 = [collector.cell.up, collector.cell.up.left, collector.cell.up.right, collector.cell.up.up]
        elif move == 'DOWN':
            check_cell_list_2 = [collector.cell.down, collector.cell.down.left, collector.cell.down.right, collector.cell.down.down]
        elif move == 'RIGHT':
            check_cell_list_2 = [collector.cell.right, collector.cell.right.up, collector.cell.right.down, collector.cell.right.right]
        elif move == 'LEFT':
            check_cell_list_2 = [collector.cell.left, collector.cell.left.up, collector.cell.left.down, collector.cell.left.left]
        else:
            raise NotImplementedError


        global overall_plan
        term2 = all([False if (_c.collector is not None and (_c.collector.carbon<collector.carbon and
                                _c.collector.player_id!=collector.player_id)) else True for _c in check_cell_list_2])

        term3 = all([False if (_c.recrtCenter is not None and _c.recrtCenter.player_id!=collector.player_id) else True
                     for _c in check_cell_list])
        #term2 = True
        return all([True if ((_c.collector is None or (_c.collector.carbon<collector.carbon and
                                _c.collector.player_id!=collector.player_id))
                             and (not _c.position in overall_plan)
                             ) or ((_c.recrtCenter is not None)) or steps>290
                    else False for _c in check_cell_list]) and term2 and term3

    def _check_surround_validity_cell(self, collector: Collector, steps) -> bool:
        check_cell_list = [collector.cell]
        check_cell_list_2 = [collector.cell.up, collector.cell.left, collector.cell.right, collector.cell.down]

        global overall_plan
        term2 = all([False if (_c.collector is not None and (_c.collector.carbon<collector.carbon and
                                _c.collector.player_id!=collector.player_id)) else True for _c in check_cell_list_2])
        #term2 = True
        return all([True if (not _c.position in overall_plan)
                              or ((_c.recrtCenter is not None) and steps>270) or steps>290
                    else False for _c in check_cell_list]) and term2

    def get_min_oppo_dis(self, collector, oppo_info):

        min_dis = 1000000
        collector_position = collector.position
        for oppo_collector in oppo_info[0].collectors:
            if oppo_collector.carbon < collector.carbon:
                continue
            _cell = oppo_collector.cell
            collector_to_cell_distance = self._calculate_distance(collector_position, _cell.position)

            if  collector_to_cell_distance< min_dis:
                min_dis = collector_to_cell_distance
                max_score_cell = _cell

        for oppo_planter in oppo_info[0].planters:
            _cell = oppo_planter.cell
            collector_to_cell_distance = self._calculate_distance(collector_position, _cell.position)

            if  collector_to_cell_distance< min_dis:
                min_dis = collector_to_cell_distance
                max_score_cell = _cell

        return min_dis

    def move(self, ours_info, oppo_info, **kwargs):
        global overall_plan, attacker_sum
        move_action_dict = dict()

        """需要知道本方当前位置信息，敵方当前位置信息，地图上的碳的分布"""
        # 如果planter信息是空的，则无需执行任何操作
        if ours_info.collectors == []:
            return None

        map_carbon_cell = kwargs["map_carbon_location"]
        carbon_sort_dict = calculate_carbon_contain_single(map_carbon_cell)  # 每一次move都先计算一次附近碳多少的分布

        self.rct_attacker_id = -1
        dis_list = []
        min_dis = 100000
        for collector in ours_info.collectors:
            # 先给他随机初始化一个行动
            if collector.carbon>0:
                continue
            #attacker = False
            tmp_dis = self._calculate_distance(collector.position, oppo_info[0].recrtCenters[0].position)
            min_dis = min(tmp_dis, min_dis)
            if tmp_dis == min_dis:
                self.rct_attacker_id = collector.id
            dis_list.append(self.get_min_oppo_dis(collector, oppo_info))

        if ours_info.cash<400:
            self.rct_attacker_id = -1
        dis_list.sort()

        attacker_sum = min(len(ours_info.collectors)//2, len(dis_list)-1)
        if attacker_sum<=0:
            thresh = 0
        else:
            thresh = dis_list[attacker_sum-1]

        attacker_sum = 0
        go_home_sum = 0
        for collector in ours_info.collectors:
            # 先给他随机初始化一个行动
            attacker = False
            #if self.get_min_oppo_dis(collector, oppo_info)<=thresh:
            #    attacker = True

            attacker = False
            if self.get_min_oppo_dis(collector, oppo_info)<=2 and collector.carbon<10:
                if random.random()<0.7:
                    attacker = True
                    attacker_sum += 1

            if self.get_min_oppo_dis(collector, oppo_info)<=1 and collector.carbon<40:
                attacker = True
                attacker_sum += 1

            if attacker_sum<=0:
                attacker = False

            attacker_sum-=attacker
            #if collector.carbon==0 and collector.position==ours_info.recrtCenters[0].position:#self._calculate_distance(collector.position, oppo_info[0].recrtCenters[0].position)<5:
            #    if random.random() < 0.:
            #        self.attacker = True
            #    else:
            #        self.attacker = False
            dis_to_home = self._calculate_distance(ours_info.recrtCenters[0].position, collector.position)
            dis_to_home = min(dis_to_home, self._calculate_distance(ours_info.recrtCenters[1].position, collector.position))
            carbon_thresh = 100
            if ours_info.cash<30:
                carbon_thresh = 30
            if ours_info.cash > 500:
                carbon_thresh = 130
            if ours_info.cash>2000:
                carbon_thresh = 250 + dis_to_home*10
            if collector.id not in self.collector_target:  # 说明他还没有策略，要为其分配新的策略
                if attacker:
                    target_cell = self._target_plan_attacker(collector, ours_info, oppo_info)
                elif collector.carbon<carbon_thresh and (300-kwargs['step'] >= dis_to_home+9):
                    target_cell = self._target_plan(collector, carbon_sort_dict, ours_info, oppo_info)  # 返回这个智能体要去哪里的一个字典
                elif (300-kwargs['step']<dis_to_home+9):
                    target_cell = ours_info.recrtCenters[0].cell
                else:
                    #if go_home_sum >= len(ours_info.collectors)/4*3:
                    #    target_cell = self._target_plan(collector, carbon_sort_dict, ours_info, oppo_info)
                    #else:
                    go_home_sum += 1
                    target_cell = self._target_plan_2_home(collector, carbon_sort_dict, ours_info)
                self.collector_target[collector.id] = target_cell  # 给它新的行动
            #else:  # 说明他有策略，看策略是否执行完毕，执行完了移出字典，没有执行完接着执行
            if collector.position == self.collector_target[collector.id].position:
                if self._check_surround_validity_cell(collector, kwargs['step']):
                    overall_plan[collector.position] = 1
                else:
                    filtered_list_act = WorkerAction.moves()
                    for move in WorkerAction.moves():
                        if not self._check_surround_validity(move, collector, kwargs['step']):
                            filtered_list_act.remove(move)
                    if len(filtered_list_act) == 0:
                        filtered_list_act.append(move)
                    if not collector.id in move_action_dict:
                        tmp = random.choice(filtered_list_act)
                        move_action_dict[collector.id] = tmp.name
                        new_position = cal_new_pos(collector.position, tmp)
                        overall_plan[new_position] = 1
                self.collector_target.pop(collector.id)
            else:  # 没有执行完接着执行
                old_position = collector.position
                target_position = self.collector_target[collector.id].position
                old_distance = self._calculate_distance(old_position, target_position)

                filtered_list_act = WorkerAction.moves()
                for move in WorkerAction.moves():
                    if not self._check_surround_validity(move, collector, kwargs['step']):
                        filtered_list_act.remove(move)

                best_move = WorkerAction.UP
                best_choice = []
                for move in WorkerAction.moves():
                    new_position = cal_new_pos(old_position, move)
                    new_distance = self._calculate_distance(new_position, target_position)

                    if new_distance < old_distance:
                        best_move = move
                        if self.cal_cell_tree_num(
                                cal_new_pos_cell(collector.cell, move), ours_info) >= 2 and collector.carbon > 50:
                            continue
                        if self.cal_cell_tree_num(cal_new_pos_cell(collector.cell, move), ours_info)>=1 and collector.carbon>80:
                            continue
                        if self._check_surround_validity(move, collector, kwargs['step']):
                            best_choice.append(move)
                            continue
                            #move_action_dict[collector.id] = move.name
                            #overall_plan[new_position] = 1
                            #break
                if len(best_choice)>0:
                    move = random.choice(best_choice)
                    new_position = cal_new_pos(old_position, move)
                    move_action_dict[collector.id] = move.name
                    overall_plan[new_position] = 1

                #if len(filtered_list_act) == 0:
                #    filtered_list_act.append(move)
                if not attacker and self._check_surround_validity_cell(collector, kwargs['step']):
                    filtered_list_act.append('')
                if len(filtered_list_act) == 0:
                    filtered_list_act.append(best_move)
                if not collector.id in move_action_dict:
                    tmp = random.choice(filtered_list_act)
                    if tmp!='':
                        move_action_dict[collector.id] = tmp.name
                        new_position = cal_new_pos(old_position, tmp)
                        overall_plan[new_position] = 1
                    else:
                        #move_action_dict.pop(collector.id)
                        overall_plan[old_position] = 1

                self.collector_target.pop(collector.id) #每步决策

        return move_action_dict

def cal_new_pos(pos, move):
    new_position = pos + move.to_point()
    new_position = Point(*eval(str(new_position).replace("15", "0")))
    new_position = Point(*eval(str(new_position).replace("-1", "14")))
    return new_position

def cal_new_pos_cell(cell, move):
    new_cell = cell
    if move.name=='UP':
        new_cell = cell.up
    if move.name == 'DOWN':
        new_cell = cell.down
    if move.name == 'LEFT':
        new_cell = cell.left
    if move.name == 'RIGHT':
        new_cell = cell.right
    return new_cell

class PlanterAct(AgentBase):
    def __init__(self):
        super().__init__()
        self.workaction = WorkerAction
        self.planter_target = dict()

    @ staticmethod
    def _minimum_distance(point_1, point_2):
        abs_distance = abs(point_1 - point_2)
        cross_distance = min(point_1, point_2) + (15 - max(point_1, point_2))  # TODO: 这里对吗，是14减?
        return min(abs_distance, cross_distance)

    def _calculate_distance(self, planter_position, current_position):
        """计算真实距离，计算跨图距离，取两者最小值"""
        x_distance = self._minimum_distance(planter_position[0], current_position[0])
        y_distance = self._minimum_distance(planter_position[1], current_position[1])

        return x_distance + y_distance

    def _target_plan(self, planter: Planter, carbon_sort_dict: Dict, ours_info, oppo_info):
        """结合某一位置的碳的含量和距离"""
        # TODO：钱够不够是否考虑？
        planter_position = planter.position
        # 取碳排量最高的前十
        carbon_sort_dict_top_n = \
            {_v: _k for _i, (_v, _k) in enumerate(carbon_sort_dict.items()) if _i < 100}  # 只选取含碳量top_n的cell来进行计算，拿全部的cell可能会比较耗时？
        # 计算planter和他的相对距离，并且结合该位置四周碳的含量，得到一个总的得分
        planned_target = [Point(*_v.position) for _k, _v in self.planter_target.items()]
        max_score, max_score_cell = -1e9, None
        for _cell, _carbon_sum in carbon_sort_dict_top_n.items():
            if (_cell.tree is None) and (_cell.position not in planned_target) and (_cell.recrtCenter is None):  # 这个位置没有树，且这个位置不在其他智能体正在进行的plan中
                planter_to_cell_distance = self._calculate_distance(planter_position, _cell.position)  # 我们希望这个距离越小越好
                target_preference_score = 0 * _carbon_sum + np.log(1 / (planter_to_cell_distance + 1e-9)) # 不考虑碳总量只考虑距离 TODO: 这会导致中了很多树，导致后期花费很高
                target_preference_score = _carbon_sum *1.5 + planter_to_cell_distance * (-25)

                if planter_to_cell_distance == 0:
                    target_preference_score += _carbon_sum * 1.2

                if planter_to_cell_distance==1:
                    target_preference_score += _carbon_sum * 0.7

                if self.cal_cell_tree_num(_cell) > 0:
                    target_preference_score -= 400

                if self.cal_cell_tree_num(_cell)>1:
                    target_preference_score -= 400

                #target_preference_score = min(_carbon_sum*(1.05**planter_to_cell_distance)/(planter_to_cell_distance+1), 200)

                if target_preference_score > max_score:
                    max_score = target_preference_score
                    max_score_cell = _cell

        if planter.player_id==0:
            center = (10, 4)
        else:
            center = (4, 10)

        for _cell, _carbon_sum in carbon_sort_dict.items():
            if self.expendable_id==planter.id and _cell.position!=center \
                    and (_cell.tree is None or _cell.tree.player_id != planter.player_id)\
                    and abs(_cell.position[0]-oppo_info[0].recrtCenters[0].position[0])==1\
                    and abs(_cell.position[1]-oppo_info[0].recrtCenters[0].position[1])==1:

                planter_to_cell_distance = self._calculate_distance(planter_position, _cell.position)
                target_preference_score = 10000 + planter_to_cell_distance * (-20) #+_carbon_sum + planter_to_cell_distance * (-20) #+ 10*len(oppo_info[0].collectors)
                if target_preference_score > max_score:
                    max_score = target_preference_score
                    max_score_cell = _cell

            if not _cell.tree is None:
                if _cell.tree.player_id != planter.player_id:
                    cell_to_center_dis = self._calculate_distance(_cell.position, ours_info.recrtCenters[0].position)
                    planter_to_cell_distance = self._calculate_distance(planter_position, _cell.position)
                    if 50 - (_cell.tree.age + planter_to_cell_distance) <10 and cell_to_center_dis>2:
                        continue
                    target_preference_score = 0 * _carbon_sum + np.log(1 / (planter_to_cell_distance + 1e-9)) + 200
                    if len(ours_info.trees)+len(oppo_info[0].trees)>8:
                        pri = 500
                    else:
                        pri = 200

                    _carbon_sum = (50 - (_cell.tree.age + planter_to_cell_distance))*8
                    target_preference_score = _carbon_sum + planter_to_cell_distance * (-30) + pri + \
                                              (self.cal_tree_money(len(ours_info.trees)+len(oppo_info[0].trees)) - 20)*1.5
                    if cell_to_center_dis<=2:
                        target_preference_score += 1000
                    else:
                        target_preference_score += 1/cell_to_center_dis * 500
                    #if planter_to_cell_distance>4:
                    #    target_preference_score -= 2**planter_to_cell_distance
                    if target_preference_score > max_score:
                        max_score = target_preference_score
                        max_score_cell = _cell

        if max_score_cell is None:  # 没有找到符合条件的最大得分的cell，随机选一个cell
            max_score_cell = random.choice(list(carbon_sort_dict_top_n))

        return max_score_cell

    def _check_surround_validity(self, move: WorkerAction, planter: Planter) -> bool:
        move = move.name
        if move == 'UP':
            # 需要看前方三个位置有没有Agent
            check_cell_list = [planter.cell.up, planter.cell.up.left, planter.cell.up.right, planter.cell.up.up]
        elif move == 'DOWN':
            check_cell_list = [planter.cell.down, planter.cell.down.left, planter.cell.down.right, planter.cell.down.down]
        elif move == 'RIGHT':
            check_cell_list = [planter.cell.right, planter.cell.right.up, planter.cell.right.down, planter.cell.right.right]
        elif move == 'LEFT':
            check_cell_list = [planter.cell.left, planter.cell.left.up, planter.cell.left.down, planter.cell.left.left]
        else:
            raise NotImplementedError

        global overall_plan
        return all([True if ((_c.collector is None or (_c.collector.player_id == planter.player_id)) and
                             (not _c.position in overall_plan)) else False for _c in check_cell_list])

    def _check_surround_validity_cell(self, planter: Planter) -> bool:
        check_cell_list = [planter.cell]
        check_cell_list_2 = [planter.cell.up, planter.cell.left, planter.cell.right, planter.cell.down]

        global overall_plan
        term2 = all([False if (_c.collector is not None and (_c.collector.player_id!=planter.player_id)) else True for _c in check_cell_list_2])
        #term2 = True
        term1 = all([False if _c.position in overall_plan else True for _c in check_cell_list])
        return term2 and term1

    def cal_tree_money(self, tree_num):
        return 5 * 1.235 ** tree_num

    def cal_cell_tree_num(self, cell):
        check_cell_list = [cell.left, cell.right, cell.down, cell.up, cell.up.right, cell.up.left,
                           cell.down.left, cell.down.right,
                           cell.left.left, cell.right.right, cell.down.down, cell.up.up]
        return sum([False if (_c.tree is None) else True for _c in check_cell_list])

    def move(self, ours_info, oppo_info, **kwargs):
        global overall_plan
        move_action_dict = dict()

        """需要知道本方当前位置信息，敵方当前位置信息，地图上的碳的分布"""
        # 如果planter信息是空的，则无需执行任何操作
        if ours_info.planters == []:
            return None

        map_carbon_cell = kwargs["map_carbon_location"]
        carbon_sort_dict = calculate_carbon_contain(map_carbon_cell)  # 每一次move都先计算一次附近碳多少的分布

        min_dis = 100000
        expendable = -1
        self.expendable_id = -1

        for planter in ours_info.planters:
            planter_to_oppo_dis = self._calculate_distance(planter.position, oppo_info[0].recrtCenters[0].position)
            min_dis = min(min_dis, planter_to_oppo_dis)
            if min_dis==planter_to_oppo_dis and len(oppo_info[0].collectors)>2 and ours_info.cash>800:
                self.expendable_id = planter.id

        for planter in ours_info.planters:
            # 先给他随机初始化一个行动
            if planter.id not in self.planter_target:   # 说明他还没有策略，要为其分配新的策略
                target_cell = self._target_plan(planter, carbon_sort_dict, ours_info, oppo_info)  # 返回这个智能体要去哪里的一个字典
                self.planter_target[planter.id] = target_cell  # 给它新的行动
            #else:  # 说明他有策略，看策略是否执行完毕，执行完了移出字典，没有执行完接着执行
            if planter.position == self.planter_target[planter.id].position:
                # 执行一次种树行动, TODO: 如果钱够就种树，钱不够不执行任何操作
                # move_action_dict[planter.id] = None
                # TODO: 这里不执行任何行动就表示种树了？
                # 移出字典
                money = 20 + self.cal_tree_money(len(ours_info.trees)+len(oppo_info[0].trees))
                if (planter.cell.tree is not None) and planter.cell.tree.player_id!=planter.player_id:
                    money = 20
                carbon = cal_carbon_one_cell(planter.cell, map_carbon_cell)
                if (((money * 1.4 < carbon or money<50) and (kwargs['step']<295)) or self.expendable_id==planter.id)\
                        and self._check_surround_validity_cell(planter):
                    overall_plan[planter.position] = 1
                else:
                    filtered_list_act = WorkerAction.moves()
                    for move in WorkerAction.moves():
                        if not self._check_surround_validity(move, planter):
                            filtered_list_act.remove(move)

                    if len(filtered_list_act) == 0:
                        filtered_list_act.append(move)
                    if not planter.id in move_action_dict:
                        tmp = random.choice(filtered_list_act)
                        move_action_dict[planter.id] = tmp.name
                        new_position = cal_new_pos(planter.position, tmp)
                        overall_plan[new_position] = 1

                self.planter_target.pop(planter.id)

            else:  # 没有执行完接着执行
                old_position = planter.position
                target_position = self.planter_target[planter.id].position
                old_distance = self._calculate_distance(old_position, target_position)

                filtered_list_act = WorkerAction.moves()
                for move in WorkerAction.moves():
                    if not self._check_surround_validity(move, planter):
                        filtered_list_act.remove(move)

                for move in WorkerAction.moves():
                    new_position = cal_new_pos(old_position, move)
                    new_distance = self._calculate_distance(new_position, target_position)

                    if new_distance < old_distance:
                        if self._check_surround_validity(move, planter):
                            move_action_dict[planter.id] = move.name
                            overall_plan[new_position] = 1
                            break

                if len(filtered_list_act)==0:
                    filtered_list_act.append(move)
                if not planter.id in move_action_dict:
                    tmp = random.choice(filtered_list_act)
                    move_action_dict[planter.id] = tmp.name
                    new_position = cal_new_pos(old_position, tmp)
                    overall_plan[new_position] = 1

                self.planter_target.pop(planter.id)

        return move_action_dict


class RecruiterAct(AgentBase):
    def __init__(self):
        super().__init__()

    def action(self, ours_info, oppo_info, **kwargs):
        store_dict = dict()

        global overall_plan

        for r_id in range(2):
            if not ours_info.recrtCenters[r_id].cell.position in overall_plan:
                if len(ours_info.planters) < 3 \
                        and len(ours_info.collectors) >= 7:
                    store_dict[ours_info.recrtCenters[r_id].id] = RecrtCenterAction.RECPLANTER.name
                else:
                    store_dict[ours_info.recrtCenters[r_id].id] = RecrtCenterAction.RECCOLLECTOR.name

                if len(ours_info.planters) < 1 and len(ours_info.collectors) > 0:
                    store_dict[ours_info.recrtCenters[r_id].id] = RecrtCenterAction.RECPLANTER.name

                if len(ours_info.planters) < 2 and len(ours_info.collectors) > 0:
                    store_dict[ours_info.recrtCenters[r_id].id] = RecrtCenterAction.RECPLANTER.name

                if len(ours_info.planters) > 0 and ours_info.cash < 50:
                    store_dict.pop(ours_info.recrtCenters[r_id].id)

                if (ours_info.recrtCenters[r_id].cell.collector is not None and
                        ours_info.recrtCenters[r_id].cell.collector.player_id != ours_info.recrtCenters[0].player_id):
                    store_dict[ours_info.recrtCenters[r_id].id] = RecrtCenterAction.RECCOLLECTOR.name

            else:
                pass

        return store_dict


class PlanningPolicy(BasePolicy):
    def __init__(self, ):
        super().__init__()
        # self.worker = WorkerAct()
        self.collector = CollectorAct()
        self.planter = PlanterAct()
        self.recruiter = RecruiterAct()

    def take_action(self, current_obs: Board, previous_obs: Board) -> Dict:
        global overall_plan
        overall_plan = dict()
        overall_dict = dict()

        ours, oppo = current_obs.current_player, current_obs.opponents

        # 种树员做决策去哪里种树
        planter_dict = self.planter.move(
            ours_info=ours,
            oppo_info=oppo,
            map_carbon_location=current_obs.cells,
            step=current_obs.step,
        )

        if planter_dict is not None:
            overall_dict.update(planter_dict)

        collector_dict = self.collector.move(
            ours_info=ours,
            oppo_info=oppo,
            map_carbon_location=current_obs.cells,
            step=current_obs.step,
        )

        if collector_dict is not None:
            overall_dict.update(collector_dict)

        # 基地先做决策是否招募
        recruit_dict = self.recruiter.action(
            ours_info=ours,
            oppo_info=oppo[0],
            map_carbon_location=current_obs.cells,
            step=current_obs.step,
        )

        # 这里要进行一个判断，确保基地位置没有智能体才能招募下一个

        if recruit_dict is not None:
            overall_dict.update(recruit_dict)

        return overall_dict

        # 对于我方每一个捕碳员，不采取任何行动


class MyPolicy:

    def __init__(self):
        # self.obs_parser = ObservationParser()
        self.policy = PlanningPolicy()

    def take_action(self, observation, configuration):
        global attacker_sum
        attacker_sum = 0
        current_obs = Board(observation, configuration)
        previous_obs = self.previous_obs if current_obs.step > 0 else None

        overall_action = self.policy.take_action(current_obs=current_obs, previous_obs=previous_obs)
        # overall_action = self.to_env_commands(overall_action)

        # agent_obs_dict, dones, available_actions_dict = self.obs_parser.obs_transform(current_obs, previous_obs)
        self.previous_obs = copy.deepcopy(current_obs)

        return overall_action

my_policy = MyPolicy()

def supplementary_agent(obs, configuration):
    global my_policy
    commands = my_policy.take_action(obs, configuration)
    return commands


def agent(obs, configuration):
    def choose_agent(board:Board):
        worker_count=len(board.workers)
        tree_count=len(board.trees)
        step=board.step
        player_money=board.current_player.cash

        # initialize weights
        worker_weight = 10
        tree_weight = 5
        step_count = 8
        player_money_weight =3
        bias = -10
        output=worker_count * worker_weight + tree_count * tree_weight + step * step_count + player_money *player_money_weight + bias
        rand_output = random.randint(-1,9)
        if rand_output > 0:
            return choose_agent(board)
        else:
            if output>0:
                return "supplementary_agent"
            else:
                return "model_agent"

    board = Board(obs, configuration)
    choosen_agent = choose_agent(board)
    # print(choosen_agent)
    commands = [PlanterAct]
    if choosen_agent == "supplementary_agent":
        commands = supplementary_agent(obs, configuration)
    elif choosen_agent == "model_agent":
        commands = model_agent(obs, configuration)
    return commands