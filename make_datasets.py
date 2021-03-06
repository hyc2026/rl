import argparse
from functools import total_ordering
from datasets_make.final_jds_1228 import MyPolicy as daishengPolicy
import sys
from zerosum_env import make, evaluate
from zerosum_env.envs.carbon.helpers import *
import numpy as np
import json
from submission import ObservationParser
import time
import os
from tqdm import tqdm
import random
import multiprocessing
import os
BaseActions = [None,
               RecrtCenterAction.RECCOLLECTOR,
               RecrtCenterAction.RECPLANTER]

WorkerActions = [None,
                 WorkerAction.UP,
                 WorkerAction.RIGHT,
                 WorkerAction.DOWN,
                 WorkerAction.LEFT]


WorkerDirections = np.stack([np.array((0, 0)),
                             np.array((0, 1)),
                             np.array((1, 0)),
                             np.array((0, -1)),
                             np.array((-1, 0))])

def one_hot_np(value: int, num_cls: int):
    ret = np.zeros(num_cls)
    ret[value] = 1
    return ret

class ObservationParser:
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

        player_base_cmds = {player_id: BaseActions[0]
                            for player_id in current_obs.players.keys()} if current_obs is not None else {}
        total_worker_ids = set(previous_workers.keys()) | set(current_workers.keys())  # worker id列表
        for worker_id in total_worker_ids:
            previous_worker, worker = previous_workers.get(worker_id, None), current_workers.get(worker_id, None)
            if previous_worker is not None and worker is not None:  # (连续两局存活) 移动/停留 动作
                prev_pos = np.array([previous_worker.position.x, previous_worker.position.y])
                curr_pos = np.array([worker.position.x, worker.position.y])

                # 计算所有方向的可能位置 (防止越界问题)
                next_all_positions = ((prev_pos + WorkerDirections) + self.grid_size) % self.grid_size
                dir_index = (next_all_positions == curr_pos).all(axis=1).nonzero()[0].item()
                cmd = WorkerActions[dir_index]

                return_value[worker_id] = cmd
            elif previous_worker is None and worker is not None:  # (首次出现) 招募 动作
                if worker.is_collector:
                    player_base_cmds[worker.player_id] = BaseActions[1]
                elif worker.is_planter:
                    player_base_cmds[worker.player_id] = BaseActions[2]
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
            2) cnn_feature: (CNN特征, dim: 11x15x15)
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

            action_feature[base_x, base_y] = one_hot_np(previous_action.get(base_id, 0), self.action_space)
            if is_myself:
                available_actions[base_id] = np.array([1, 1, 1, 0, 0])  #

                my_base_distance_feature = distance_features[base_id]

        for worker_id, worker in current_obs.workers.items():
            is_myself = worker.player_id == my_player_id

            available_actions[worker_id] = np.array([1, 1, 1, 1, 1])  #

            worker_x, worker_y = worker.position.x, worker.position.y
            distance_features[worker_id] = self._distance_feature(worker_x, worker_y) / (self.grid_size - 1)

            action_feature[worker_x, worker_y] = one_hot_np(previous_action.get(worker_id, 0), self.action_space)

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


def run_one_episode(player1Policy, player2Policy="random"):
    try:
        player1Policy.reset_record()
    except:
        pass
    try:
        player2Policy.reset_record()
    except:
        pass
    e = make(environment="carbon", 
             configuration={"randomSeed": random.randint(1,2147483646)},
             steps=[],
             debug=False,
             state=None)
    player1Policy_action = "random" if player1Policy == "random" else player1Policy.take_action
    player2Policy_action = "random" if player2Policy == "random" else player2Policy.take_action
    agents = [player1Policy_action, player2Policy_action]
    last_state = e.run(agents)[-1]
    rewards = [state.reward for state in last_state]
    player1Policy_reward = rewards[0] or -1
    player2Policy_reward = rewards[1] or -1
    if player1Policy_reward >= player2Policy_reward:
        try:
            collect_records = player1Policy.record_list
        except:
            collect_records = player2Policy.record_list
    else:
        try:
            collect_records = player2Policy.record_list
        except:
            collect_records = player1Policy.record_list

    return collect_records

Action2ID = {
    RecrtCenterAction.RECCOLLECTOR.name: 1,
    RecrtCenterAction.RECPLANTER.name: 2,
    WorkerAction.UP.name: 1,
    WorkerAction.RIGHT.name: 2,
    WorkerAction.DOWN.name: 3,
    WorkerAction.LEFT.name: 4
}

CenterActionId2Action = {
    0 : None,
    1 : RecrtCenterAction.RECCOLLECTOR,
    2 : RecrtCenterAction.RECPLANTER
}

WorkerActionId2Action = {
    0 : None,
    1 : WorkerAction.UP,
    2 : WorkerAction.RIGHT,
    3 : WorkerAction.DOWN,
    4: WorkerAction.LEFT
}

def action2id(agentid, action):
    return Action2ID[action]

def id2action(agentid, action):
    if "Center" in agentid:
        return CenterActionId2Action[action]
    else:
        return WorkerActionId2Action[action]

def trans_policy_result(policy_result):
    agent2action = {}
    for agent_id, action in policy_result.items():
        agent2action[agent_id] = action2id(agent_id, action)
    return agent2action

def transfer_ob_feature_to_model_feature(ob_result, label_agent2action=None, masked_map=None):

    map_feature = {}
    agent_info = []
    for agent_id, ob_features in ob_result.items():
        step_features = ob_features[0]
        my_cash = ob_features[1]
        opponent_cash = ob_features[2]
        agent_type = ob_features[3:6].tolist()
        x = ob_features[6]
        y = ob_features[7]
        index_begin = 8
        carbon_feature = ob_features[index_begin: index_begin + 15*15].reshape(15, 15).tolist()
        index_begin += 15 * 15
        base_feature = ob_features[index_begin: index_begin + 15 * 15].reshape(15, 15).tolist()
        index_begin += 15 * 15
        collector_feature = ob_features[index_begin: index_begin + 15 * 15].reshape(15, 15).tolist()
        index_begin += 15 * 15
        planter_feature = ob_features[index_begin: index_begin + 15 * 15].reshape(15, 15).tolist() 
        index_begin += 15 * 15
        worker_carbon_feature = ob_features[index_begin: index_begin + 15 * 15].reshape(15, 15).tolist()  
        index_begin += 15 * 15
        tree_feature = ob_features[index_begin: index_begin + 15 * 15].reshape(15, 15).tolist()
        index_begin += 15 * 15
        action_feature = ob_features[index_begin: index_begin + 5 * 15 * 15].reshape(5, 15, 15).tolist() 
        index_begin += 5 * 15 * 15
        my_base_distance_feature = ob_features[index_begin: index_begin + 15 * 15].reshape(15, 15).tolist() 
        index_begin += 15 * 15
        distance_features = ob_features[index_begin: index_begin + 15 * 15].reshape(15, 15).tolist()

        map_feature["step_features"] = step_features
        map_feature["my_cash"] = my_cash
        map_feature["opponent_cash"] = opponent_cash
        map_feature["carbon_feature"] = carbon_feature
        map_feature["base_feature"] = base_feature
        map_feature["collector_feature"] = collector_feature
        map_feature["planter_feature"] = planter_feature
        map_feature["worker_carbon_feature"] = worker_carbon_feature
        map_feature["tree_feature"] = tree_feature
        map_feature["action_feature"] = action_feature
        map_feature["my_base_distance_feature"] = my_base_distance_feature
        map_feature["masked_map"] = masked_map

        agent_info.append((
            agent_id,
            x,
            y,
            agent_type,
            distance_features,
            label_agent2action.get(agent_id, 0) if label_agent2action is not None else -1
        ))
    return map_feature, agent_info

def collect_data(player1Policy, player2Policy="random", episode_count=1):
    data_list = []
    ob_parser = ObservationParser()
    for _ in tqdm(range(episode_count), total=episode_count):
        run_records = run_one_episode(player1Policy, player2Policy)
        for overall_action, current_obs, previous_obs, masked_map in run_records:
            local_obs, dones, available_actions = ob_parser.obs_transform(current_obs, previous_obs)
            label_agent2action = trans_policy_result(overall_action)
            map_features, agent_info = transfer_ob_feature_to_model_feature(local_obs, label_agent2action, masked_map)
            item = {
                "map_features": map_features,
                "agent_info": agent_info
            }
            data_list.append(item)
    return data_list

def dummy_main(save_file_name,episode_count):
    candidate_player_pairs = [
        (daishengPolicy(), "random", 0.25),
        ("random", daishengPolicy(), 0.25),
        (daishengPolicy(), daishengPolicy(), 0.25),
        (daishengPolicy(), daishengPolicy(), 0.25)
    ]
    all_collect_data_list = []
    for player1, player2, p in candidate_player_pairs:
        cur_episode_count = int(p * episode_count)
        if cur_episode_count <= 0:
            continue
        data_list = collect_data(player1, player2, cur_episode_count)
        all_collect_data_list.extend(data_list)

    import pickle
    import os
    if not os.path.exists('data'):
        os.mkdir('data')
    with open("data/data" + save_file_name, 'wb') as f:
        pickle.dump(all_collect_data_list, f)
    

def main_multiprocessing():
    '''
    each process run dummy_main function. Create worker_count processes. when some process exits, create a new one immediately.
    '''
    parser=argparse.ArgumentParser()
    parser.add_argument('--worker_count', type=int, default=8)
    parser.add_argument('--episode_count', type=int, default=50)
    args=parser.parse_args()
    if not os.path.exists('data'):
        os.mkdir('data')
    def worker(worker_id,episode_count):
        while True:
            try:
                save_file_name = str(time.time()*args.worker_count+worker_id)
                dummy_main(save_file_name,episode_count)
                time.sleep(1)
            except:
                print("warning: Error during making dataset,skipping")
    processes = []
    for i in range(args.worker_count):
        p = multiprocessing.Process(target=worker,kwargs={"worker_id":i,"episode_count":args.episode_count})
        p.start()
        processes.append(p)
    for p in processes:
        p.join()

if __name__ == "__main__":
    main_multiprocessing()
        



