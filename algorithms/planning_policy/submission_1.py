from os import cpu_count
import sys
import numpy as np
import torch
import copy
from abc import ABC, ABCMeta, abstractmethod
from typing import Dict, Tuple
from zerosum_env.envs.carbon.helpers import (Board, Cell, Collector, Planter,
                                             Point, RecrtCenter, Worker,
                                             RecrtCenterAction, WorkerAction)
from random import randint, shuffle

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
            # hack here, ???????????????????????????,???????????????????????????
            actions = BaseActions if 'recrtCenter' in agent_name else WorkerActions
            return actions[command_value].name if 0 < command_value < len(actions) else None

        env_commands = {}
        for agent_id, cmd_value in policy_actions.items():
            command = agent_action(agent_id, cmd_value)
            if command is not None:
                env_commands[agent_id] = command
        return env_commands

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
                             np.array((-1, 0))])  # ???WorkerActions?????????


def get_cell_carbon_after_n_step(board: Board, position: Point, n: int) -> float:
    # ??????position???????????????????????????n???????????????????????????????????????????????????????????????????????????
    danger_zone = []
    x_left = position.x - 1 if position.x > 0 else 14
    x_right = position.x + 1 if position.x < 14 else 0
    y_up = position.y - 1 if position.y > 0 else 14
    y_down = position.y + 1 if position.y < 14 else 0
    # ????????????4?????????
    danger_zone.append(Point(position.x, y_up))
    danger_zone.append(Point(x_left, position.y))
    danger_zone.append(Point(x_right, position.y))
    danger_zone.append(Point(position.x, y_down))
    
    start = 0
    target_cell = board.cells[position]
    c = target_cell.carbon
    if n == 0:
        return c
    
    # position???????????????????????????????????????????????????
    if target_cell.tree is not None:
        start = 50 - target_cell.tree.age + 1
        if start <= n:
            c = 30.0
        else:
            return 0
            
    # ???????????????????????????????????????????????????position??????
    for i in range(start, n):
        tree_count = 0
        for p in danger_zone:
            tree = board.cells[p].tree
            # ?????????????????????
            if tree is not None:
                # i????????????????????????
                if tree.age + i <= 50:
                    tree_count += 1
        if tree_count == 0:
            c = c * (1.05)
        else:
            c = c * (1 - 0.0375 * tree_count)
            # c = c * (1 - 0.0375) ** tree_count
        c = min(c, 100)
    return c


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


    @staticmethod
    def to_env_commands(policy_actions: Dict[str, int]) -> Dict[str, str]:
        """
        Actions output from policy convert to actions environment can accept.
        :param policy_actions: (Dict[str, int]) Policy actions which specifies the agent name and action value.
        :return env_commands: (Dict[str, str]) Commands environment can accept,
            which specifies the agent name and the command string (None is the stay command, no need to send!).
        """

        def agent_action(agent_name, command_value) -> str:
            # hack here, ???????????????????????????,???????????????????????????
            actions = BaseActions if 'recrtCenter' in agent_name else WorkerActions
            return actions[command_value].name if 0 < command_value < len(actions) else None

        env_commands = {}
        for agent_id, cmd_value in policy_actions.items():
            command = agent_action(agent_id, cmd_value)
            if command is not None:
                env_commands[agent_id] = command
        return env_commands


# Plan?????????Agent????????????????????????????????????Action??????(??????????????????????????????????????????
# ???Action??????????????????????????????????????????????????????
#
# ???????????????????????????Agent????????????????????????????????????????????????Plan??????????????????Agent?????????Plan?????????(???????????????)Action
class BasePlan(ABC):
    #?????????source_agent,target?????????????????????????????????
    #source: ????????????Plan???Agent: collector,planter,recrtCenter
    #target: ???????????????Plan?????????: collector,planter,recrtCenter,cell
    def __init__(self, source_agent, target, planning_policy):
        self.source_agent = source_agent
        self.target = target
        self.planning_policy = planning_policy
        self.preference_index = None  #??????Plan??????????????????

    #??????Plan??????Action
    @abstractmethod
    def translate_to_action(self):
        pass


# ????????????????????????????????????Plans
class RecrtCenterPlan(BasePlan):
    def __init__(self, source_agent, target, planning_policy):
        super().__init__(source_agent, target, planning_policy)


# ??????Plan?????????????????????????????????
class SpawnPlanterPlan(RecrtCenterPlan):
    def __init__(self, source_agent, target, planning_policy):
        super().__init__(source_agent, target, planning_policy)
        self.calculate_score()

    #CUSTOM:????????????????????????
    #???????????????????????????????????????????????????
    #?????????????????????PlanningPolicy?????????????????????????????????Mask(??????????????????????????????)
    def calculate_score(self):
        #is valid
        if self.check_validity() == False:
            self.preference_index = self.planning_policy.config[
                'mask_preference_index']
        else:
            self_planters_count=self.planning_policy.game_state['our_player'].planters.__len__() 
            self_collectors_count =  self.planning_policy.game_state['our_player'].collectors.__len__() 
            self.preference_index =  (self.planning_policy.config['enabled_plans']['SpawnPlanterPlan']['planter_count_weight'] * self_planters_count \
                + self.planning_policy.config['enabled_plans']['SpawnPlanterPlan']['collector_count_weight'] * self_collectors_count \
                + 1) / 1000

    def check_validity(self):
        #????????????
        if self.planning_policy.config['enabled_plans'][
                'SpawnPlanterPlan']['enabled'] == False:
            return False
        #????????????
        if not isinstance(self.source_agent, RecrtCenter):
            return False
        if not isinstance(self.target, Cell):
            return False

        #????????????
        if self.source_agent.cell != self.target:
            return False

        #????????????
        if self.planning_policy.game_state['our_player'].planters.__len__() + self.planning_policy.game_state['our_player'].collectors.__len__() >= 10:
            return False

        #?????????
        if self.planning_policy.game_state[
                'our_player'].cash < self.planning_policy.game_state[
                    'configuration']['recPlanterCost']:
            return False
        return True

    def translate_to_action(self):
        if self.planning_policy.global_position_mask.get(self.source_agent.position, 0) == 0:
            self.planning_policy.global_position_mask[self.source_agent.position] = 1
            return RecrtCenterAction.RECPLANTER
        else:
            return None

# ??????Plan?????????????????????????????????
class SpawnCollectorPlan(RecrtCenterPlan):
    def __init__(self, source_agent, target, planning_policy):
        super().__init__(source_agent, target, planning_policy)
        self.calculate_score()

    #CUSTOM:????????????????????????
    #???????????????????????????????????????????????????
    #?????????????????????PlanningPolicy?????????????????????????????????Mask(??????????????????????????????)
    def calculate_score(self):
        #is valid
        if self.check_validity() == False:
            self.preference_index = self.planning_policy.config[
                'mask_preference_index']
        else:
            self_planters_count=self.planning_policy.game_state['our_player'].planters.__len__() 
            self_collectors_count =  self.planning_policy.game_state['our_player'].collectors.__len__() 
            self.preference_index =  (self.planning_policy.config['enabled_plans']['SpawnCollectorPlan']['planter_count_weight'] * self_planters_count \
                + self.planning_policy.config['enabled_plans']['SpawnCollectorPlan']['collector_count_weight'] * self_collectors_count \
                    + 1) / 1000 + 0.0001

    def check_validity(self):
        #????????????
        if self.planning_policy.config['enabled_plans'][
                'SpawnCollectorPlan']['enabled'] == False:
            return False
        #????????????
        if not isinstance(self.source_agent, RecrtCenter):
            return False
        if not isinstance(self.target, Cell):
            return False
        #????????????
        if self.planning_policy.game_state['our_player'].planters.__len__() + self.planning_policy.game_state['our_player'].collectors.__len__() >= 10:
            return False
        #????????????
        if self.source_agent.cell != self.target:
            return False
        #?????????
        if self.planning_policy.game_state[
                'our_player'].cash < self.planning_policy.game_state[
                    'configuration']['recCollectorCost']:
            return False
        return True

    def translate_to_action(self):
        if self.planning_policy.global_position_mask.get(self.source_agent.position, 0) == 0:
            self.planning_policy.global_position_mask[self.source_agent.position] = 1
            return RecrtCenterAction.RECCOLLECTOR
        else:
            return None

class PlanterPlan(BasePlan):
    def __init__(self, source_agent, target, planning_policy):
        super().__init__(source_agent, target, planning_policy)
        # self.num_of_trees = len(self.planning_policy.game_state['board'].trees())

    def check_valid(self):
        yes_it_is = isinstance(self.source_agent, Planter)
        return yes_it_is
        
    def get_distance2target(self):
        source_posotion = self.source_agent.position
        target_position = self.target.position
        distance = self.planning_policy.get_distance(
            source_posotion[0], source_posotion[1], target_position[0],
            target_position[1])
        return distance

    def get_total_carbon(self, distance=0):
        target_carbon_except = 0
        for c in [self.target.up, self.target.left, self.target.right, self.target.down]:
            target_carbon_except += get_cell_carbon_after_n_step(self.planning_policy.game_state['board'],
                                                        c.position,
                                                        distance + 1)        
        return target_carbon_except

    
    # ??????????????????????????????n?????????n?????????????????????????????????
    def get_total_carbon_predicted(self, step_number, carbon_growth_rate):
        target_sorrounding_cells = [self.target.up, self.target.down, self.target.left, self.target.right]
        total_carbon = 0
        for cell in target_sorrounding_cells:
            cur_list = [cell.up, cell.down, cell.left, cell.right]
            flag = 0
            for cur_pos in cur_list:
                if cur_pos.tree:
                    flag = 1
                    break
            if flag:
                total_carbon += cell.carbon * (1 + carbon_growth_rate) ** step_number
            else:
                total_carbon += cell.carbon
        return total_carbon


    def can_action(self, action_position):
        if self.planning_policy.global_position_mask.get(action_position, 0) == 0:
            action_cell = self.planning_policy.game_state['board']._cells[action_position]
            flag = True

            collectors = [action_cell.collector,
                          action_cell.up.collector, 
                          action_cell.down.collector,
                          action_cell.left.collector,
                          action_cell.right.collector]

            for worker in collectors:
                if worker is None:
                    continue
                # if worker.player_id == self.source_agent.player_id:
                #     continue
                return False
            
            return True
        else:
            return False

    def get_actual_plant_cost(self):
        configuration = self.planning_policy.game_state['configuration']

        if len(self.planning_policy.game_state['our_player'].tree_ids) == 0:
            return configuration.recPlanterCost
        else:
            return configuration.recPlanterCost + configuration.plantCostInflationRatio * configuration.plantCostInflationBase**self.planning_policy.game_state[
                'board'].trees.__len__()

    def translate_to_action_first(self):
        if self.source_agent.cell == self.target and self.can_action(self.target.position):
            # self.planning_policy.global_position_mask[self.target.position] = 1
            return None, self.target.position
        else:
            old_position = self.source_agent.cell.position
            old_distance = self.planning_policy.get_distance(
                old_position[0], old_position[1], self.target.position[0],
                self.target.position[1])

            move_list = []

            for i, action in enumerate(WorkerActions):
                if action == None:
                    continue
                new_position = (
                    (WorkerDirections[i][0] + old_position[0]+ self.planning_policy.config['row_count']) % self.planning_policy.config['row_count'],
                    (WorkerDirections[i][1] + old_position[1]+ self.planning_policy.config['column_count']) % self.planning_policy.config['column_count'],
                )
                new_distance = self.planning_policy.get_distance(
                    new_position[0], new_position[1], self.target.position[0],
                    self.target.position[1])
                rand_factor = randint(0, 100)
                move_list.append((action, new_position, new_distance, rand_factor))

            move_list = sorted(move_list, key=lambda x: x[2: 4])

            for move, new_position, new_d, _ in move_list:
                if self.can_action(new_position):
                    # self.planning_policy.global_position_mask[new_position] = 1
                    return move, new_position
            return None, old_position

    def translate_to_action_second(self, cash):
        cur, position = self.translate_to_action_first()
        old_position = self.source_agent.cell.position
        if cur is None:
            # ?????????
            if self.planning_policy.game_state[
               'our_player'].cash < cash:
                waiting_list = WorkerActions[0:]
                # ?????????????????????None
                waiting_list.append(None)
                shuffle(waiting_list)
                for i, action in enumerate(waiting_list):
                    if action is None:
                        self.planning_policy.global_position_mask[self.target.position] = 1
                        return None
                    new_position = (
                        (WorkerDirections[i][0] + old_position[0]+ self.planning_policy.config['row_count']) % self.planning_policy.config['row_count'],
                        (WorkerDirections[i][1] + old_position[1]+ self.planning_policy.config['column_count']) % self.planning_policy.config['column_count'],
                    )
                    if self.can_action(new_position):
                        self.planning_policy.global_position_mask[new_position] = 1
                        return action
        else:
            self.planning_policy.global_position_mask[position] = 1
            return cur


    def get_tree_absorb_carbon_speed_at_cell(self, cell: Cell):
        pass


# ????????? ????????????
class PlanterRobTreePlan(PlanterPlan):
    def __init__(self, source_agent, target, planning_policy):
        super().__init__(source_agent, target, planning_policy)
        self.calculate_score()

    def calculate_score(self):
        if self.check_validity() == False:
            self.preference_index = self.planning_policy.config[
                'mask_preference_index']
        else:
            distance = self.get_distance2target()
            if self.target.tree is None:
                self.preference_index = self.get_total_carbon(distance) / 400
                return
            if self.target.tree.player_id == self.source_agent.player_id:
                self.preference_index = 0.00001
                return 

            # source_posotion = self.source_agent.position
            # target_position = self.target.position
            # distance = self.planning_policy.get_distance(
            #     source_posotion[0], source_posotion[1], target_position[0],
            #     target_position[1])
            distance = self.get_distance2target()

            # self.preference_index = (50 - self.target.tree.age) * self.planning_policy.config[
            #     'enabled_plans']['PlanterRobTreePlan'][
            #         'cell_carbon_weight'] + distance * self.planning_policy.config[
            #             'enabled_plans']['PlanterRobTreePlan'][
            #                 'cell_distance_weight']
            total_carbon = self.get_total_carbon(distance)

            nearest_oppo_planter_distance = 10000
            age_can_use = min(50 - self.target.tree.age - distance - 1, nearest_oppo_planter_distance)
            self.preference_index = 2 * sum([total_carbon * (0.0375 ** i) for i in range(1, age_can_use + 1)])
            
            # print(self.preference_index)

    def check_validity(self):
        #????????????
        if self.planning_policy.config['enabled_plans'][
                'PlanterRobTreePlan']['enabled'] == False:
            return False
        #????????????
        if not isinstance(self.source_agent, Planter):
            return False
        if not isinstance(self.target, Cell):
            return False
        
        #if self.target.tree is None:
        #   return False
        #if self.target.tree.player_id == self.source_agent.player_id:
        #   return False
        
        return True

    def translate_to_action(self):
        return self.translate_to_action_second(20)


class PlanterPlantTreePlan(PlanterPlan):
    def __init__(self, source_agent, target, planning_policy):
        super().__init__(source_agent, target, planning_policy)
        self.calculate_score()
    
    def calculate_score(self):
        if self.check_validity() == False:
            self.preference_index = self.planning_policy.config[
                'mask_preference_index']
        else:
            distance2target = self.get_distance2target()
            my_id = self.source_agent.player_id  # ????????????id
            worker_dict = self.planning_policy.game_state['board'].workers  # ??????????????? Planter & Collector
            min_distance = 100000
            source_position = self.source_agent.position
            for work_id, cur_worker in worker_dict.items():
                if cur_worker.player_id != my_id:  # ??????worker
                    cur_pos = cur_worker.position
                    cur_dis = self.planning_policy.get_distance(source_position[0], source_position[1],
                                                                cur_pos[0], cur_pos[1])
                    if cur_dis < min_distance:
                        min_distance = cur_dis
            # ?????????????????????????????? min_distance ???????????????worker???????????????
            cur_json = self.planning_policy.config['enabled_plans']['PlanterPlantTreePlan']
            # w0, w1, w2 = cur_json['cell_carbon_weight'], cur_json['cell_distance_weight'], cur_json['enemy_min_distance_weight']
            # 'tree_damp_rate': 0.08,
            # 'distance_damp_rate': 0.999
            # self.preference_index = exp(total_carbon * w0 + distance2target * w1 + min_distance * w2)

            # 'PlanterPlantTreePlan': {
            #     'enabled': True,
            #     'cell_carbon_weight': 50,
            #     'cell_distance_weight': -40,
            #     'enemy_min_distance_weight': 50,
            #     'tree_damp_rate': 0.08,
            #     'distance_damp_rate': 0.999,
            #     'fuzzy_value': 2,
            #     'carbon_growth_rate': 0.05
            # },

            tree_damp_rate = cur_json['tree_damp_rate']
            distance_damp_rate = cur_json['distance_damp_rate']
            fuzzy_value = cur_json['fuzzy_value']
            carbon_growth_rate =cur_json['carbon_growth_rate']
            total_predict_carbon = self.get_total_carbon_predicted(distance2target, carbon_growth_rate)
            cur_index = total_predict_carbon * (distance_damp_rate ** distance2target) * (min_distance - distance2target) * fuzzy_value
            surroundings = [self.target.up, self.target.down, self.target.left, self.target.right]
            damp_count = 0
            for su in surroundings:
                cur_list = [su.up, su.down, su.left, su.down]
                for eve in cur_list:
                    if eve.tree:
                        damp_count += 1
            self.preference_index = cur_index * (1 - tree_damp_rate * damp_count)

                    
    def translate_to_action(self):
        return self.translate_to_action_second(self.get_actual_plant_cost())

            
    def check_validity(self):
        if self.planning_policy.config['enabled_plans'][
                'PlanterPlantTreePlan']['enabled'] == False:
            return False
        if self.target.tree:
            return False
        
        # if self.planning_policy.game_state[
        #        'our_player'].cash < self.get_actual_plant_cost():
        #        return False
        return True
            




class CollectorPlan(BasePlan):
    def __init__(self, source_agent, target, planning_policy):
        super().__init__(source_agent, target, planning_policy)

    def check_validity(self):
        yes_it_is = isinstance(self.source_agent, Collector)
        return yes_it_is

    def can_action(self, action_position):
        if self.planning_policy.global_position_mask.get(action_position, 0) == 0:
            action_cell = self.planning_policy.game_state['board']._cells[action_position]
            flag = True
            collectors = [action_cell.collector,
                          action_cell.up.collector, 
                          action_cell.down.collector,
                          action_cell.left.collector,
                          action_cell.right.collector]

            for collector in collectors:
                if collector is None:
                    continue
                if collector.player_id == self.source_agent.player_id:
                    continue
                if collector.carbon <= self.source_agent.carbon:
                    return False
            return True
        else:
            return False

    def translate_to_action(self):
        potential_action = None
        potential_action_position = self.source_agent.position
        potential_carbon = -1
        source_position = self.source_agent.position
        target_position = self.target.position
        source_target_distance = self.planning_policy.get_distance(
                source_position[0], source_position[1], target_position[0],
                target_position[1])

        potential_action_list = []

        for i, action in enumerate(WorkerActions):
            action_position = (
                (WorkerDirections[i][0] + source_position[0]+ self.planning_policy.config['row_count']) % self.planning_policy.config['row_count'],
                (WorkerDirections[i][1] + source_position[1]+ self.planning_policy.config['column_count']) % self.planning_policy.config['column_count'],
            )
            if not self.can_action(action_position):
                continue
                        
            target_action_distance = self.planning_policy.get_distance(
                target_position[0], target_position[1], action_position[0],
                action_position[1])
            
            source_action_distance = self.planning_policy.get_distance(
                source_position[0], source_position[1], action_position[0],
                action_position[1])
            
            potential_action_list.append((action, 
                                         action_position,
                                         target_action_distance + source_action_distance - source_target_distance,
                                         self.planning_policy.game_state['board']._cells[action_position].carbon))

        potential_action_list = sorted(potential_action_list, key=lambda x: (-x[2], x[3]), reverse=True)
        if len(potential_action_list) > 0:
            potential_action = potential_action_list[0][0]
            potential_action_position = potential_action_list[0][1]
            if potential_action == None and target_position == action_position:
                pass
            elif potential_action == None and len(potential_action_list) > 1 and potential_action_list[1][2] == 0:
                potential_action = potential_action_list[1][0]
                potential_action_position = potential_action_list[1][1]                

        self.planning_policy.global_position_mask[potential_action_position] = 1
        return  potential_action


class CollectorGoToAndCollectCarbonPlan(CollectorPlan):
    def __init__(self, source_agent, target, planning_policy):
        super().__init__(source_agent, target, planning_policy)
        self.calculate_score()
    
    def check_validity(self):
        if self.planning_policy.config['enabled_plans'][
                'CollectorGoToAndCollectCarbonPlan']['enabled'] == False:
            return False
        else:
        #????????????
            if not isinstance(self.source_agent, Collector):
                return False
            if not isinstance(self.target, Cell):
                return False
            if self.target.tree is not None:
                return False
            if self.source_agent.carbon > self.planning_policy.config['collector_config']['gohomethreshold']:
                return False
            center_position = self.planning_policy.game_state['our_player'].recrtCenters[0].position
            source_posotion = self.source_agent.position
            source_center_distance = self.planning_policy.get_distance(
                source_posotion[0], source_posotion[1], center_position[0],
                center_position[1])
            if source_center_distance >= 300 - self.planning_policy.game_state['board'].step - 4:
                return False           
        return True

    def calculate_score(self):
        if self.check_validity() == False:
            self.preference_index = self.planning_policy.config[
                'mask_preference_index']
        else:
            source_posotion = self.source_agent.position
            target_position = self.target.position
            distance = self.planning_policy.get_distance(
                source_posotion[0], source_posotion[1], target_position[0],
                target_position[1])

            self.preference_index = get_cell_carbon_after_n_step(self.planning_policy.game_state['board'], 
                                                                self.target.position,
                                                                distance) / (distance + 1)
            
    
    def translate_to_action(self):
        return super().translate_to_action()

class CollectorGoToAndGoHomeWithCollectCarbonPlan(CollectorPlan):
    def __init__(self, source_agent, target, planning_policy):
        super().__init__(source_agent, target, planning_policy)
        self.calculate_score()
    
    def check_validity(self):
        if self.planning_policy.config['enabled_plans'][
                'CollectorGoToAndGoHomeWithCollectCarbonPlan']['enabled'] == False:
            return False
        else:
        #????????????
            if not isinstance(self.source_agent, Collector):
                return False
            if not isinstance(self.target, Cell):
                return False
            if self.target.tree is not None:
                return False
            if self.source_agent.carbon <= self.planning_policy.config['collector_config']['gohomethreshold']:
                return False
            center_position = self.planning_policy.game_state['our_player'].recrtCenters[0].position
            source_posotion = self.source_agent.position
            source_center_distance = self.planning_policy.get_distance(
                source_posotion[0], source_posotion[1], center_position[0],
                center_position[1])
            if source_center_distance >= 300 - self.planning_policy.game_state['board'].step - 4:
                return False
        return True

    def calculate_score(self):
        if self.check_validity() == False:
            self.preference_index = self.planning_policy.config[
                'mask_preference_index']
        else:
            source_posotion = self.source_agent.position
            target_position = self.target.position

            center_position = self.planning_policy.game_state['our_player'].recrtCenters[0].position
            target_center_distance = self.planning_policy.get_distance(
                center_position[0], center_position[1], target_position[0],
                target_position[1])
            
            source_target_distance = self.planning_policy.get_distance(
                source_posotion[0], source_posotion[1], target_position[0],
                target_position[1])
            
            source_center_distance = self.planning_policy.get_distance(
                source_posotion[0], source_posotion[1], target_position[0],
                target_position[1])

            if target_center_distance + source_target_distance == source_center_distance:
                self.preference_index = get_cell_carbon_after_n_step(self.planning_policy.game_state['board'], 
                                                                    self.target.position,
                                                                    source_target_distance) / (source_target_distance + 1) + 100
            else:
                self.preference_index = get_cell_carbon_after_n_step(self.planning_policy.game_state['board'], 
                                                                    self.target.position,
                                                                    source_target_distance) / (source_target_distance + 1) - 100

    def translate_to_action(self):
        return super().translate_to_action() 

class CollectorGoToAndGoHomePlan(CollectorPlan):
    def __init__(self, source_agent, target, planning_policy):
        super().__init__(source_agent, target, planning_policy)
        self.calculate_score()
    
    def check_validity(self):
        if self.planning_policy.config['enabled_plans'][
                'CollectorGoToAndGoHomePlan']['enabled'] == False:
            return False
        else:
        #????????????
            if not isinstance(self.source_agent, Collector):
                return False
            if not isinstance(self.target, Cell):
                return False
            if self.target.tree is not None:
                return False
            if self.source_agent.carbon <= self.planning_policy.config['collector_config']['gohomethreshold']:
                return False

            # ???????????????????????????1
            center_position = self.planning_policy.game_state['our_player'].recrtCenters[0].position
            source_position = self.source_agent.position
            if self.planning_policy.get_distance(
                source_position[0], source_position[1], center_position[0],
                center_position[1]) > 1:
                return False
            # target ??????????????????
            target_position = self.target.position
            if target_position[0] != center_position[0] or target_position[1] != center_position[1]:
                return False

            
            
        return True

    def calculate_score(self):
        if self.check_validity() == False:
            self.preference_index = self.planning_policy.config[
                'mask_preference_index']
        else:
            self.preference_index = 10000

    def translate_to_action(self):
        if not self.can_action(self.target.position):
            self.planning_policy.global_position_mask[self.source_agent.position] = 1
            return None
        else:
            self.planning_policy.global_position_mask[self.target.position] = 1
        for move in WorkerAction.moves():
            new_position = self.source_agent.cell.position + move.to_point()
            if new_position[0] == self.target.position[0] and new_position[1] == self.target.position[1]:
                return move 


class CollectorRushHomePlan(CollectorPlan):
    def __init__(self, source_agent, target, planning_policy):
        super().__init__(source_agent, target, planning_policy)
        self.calculate_score()
    
    def check_validity(self):
        if self.planning_policy.config['enabled_plans'][
                'CollectorRushHomePlan']['enabled'] == False:
            return False
        else:
        #????????????
            if not isinstance(self.source_agent, Collector):
                return False
            if not isinstance(self.target, Cell):
                return False
            if self.target.tree is not None:
                return False

            center_position = self.planning_policy.game_state['our_player'].recrtCenters[0].position
            source_posotion = self.source_agent.position
            source_center_distance = self.planning_policy.get_distance(
                source_posotion[0], source_posotion[1], center_position[0],
                center_position[1])

            if self.target.position[0] != center_position[0] or \
                self.target.position[1] != center_position[1]:
                return False
            if self.source_agent.carbon <= 10:
                return False

            if source_center_distance < 300 - self.planning_policy.game_state['board'].step - 5:
                return False
            
        return True

    def calculate_score(self):
        if self.check_validity() == False:
            self.preference_index = self.planning_policy.config[
                'mask_preference_index']
        else:
            self.preference_index = 5000

    def translate_to_action(self):
        return super().translate_to_action() 

class PlanningPolicy(BasePolicy):
    '''
    ???????????????????????????????????????????????????:
    1. ?????????????????????
       ??????????????????:  ????????????????????????????????????????????????????????????????????????????????????

    2. ?????????????????????????????????
       ???????????????: ?????????
       ?????????: ?????????????????????????????????
    '''
    def __init__(self):
        super().__init__()
        #????????????????????????
        self.config = {
            # ??????????????????????????????????????????????????????
            'enabled_plans': {
                # ?????? ?????????????????????
                # enabled ??? true ?????????????????????????????????
                # ??????plan??????
                'SpawnPlanterPlan': {
                    'enabled': True,
                    'planter_count_weight':-8,
                    'collector_count_weight':2,
                    # 'cash_weight':2,
                    # 'constant_weight':,
                    # 'denominator_weight':
                },
                # ?????? ?????????????????????
                'SpawnCollectorPlan': {
                    'enabled': True,
                    'planter_count_weight':8,
                    'collector_count_weight':-2,
                    # 'cash_weight':2,
                    # 'constant_weight':,
                    # 'denominator_weight':
                },
                # ????????? ????????????
                'PlanterRobTreePlan': {
                    'enabled': True,
                    'cell_carbon_weight': 1,
                    'cell_distance_weight': -7
                },
                # ????????? ????????????
                'PlanterPlantTreePlan': {
                    'enabled': False,
                    'cell_carbon_weight': 50,  # cell?????????????????????
                    'cell_distance_weight': -40,  # ?????????cell??????????????????
                    'enemy_min_distance_weight': 50,  # ?????????worker????????????????????????
                    'tree_damp_rate': 0.08,  # TODO: ??????????????????????????????
                    'distance_damp_rate': 0.999,
                    'fuzzy_value': 2,
                    'carbon_growth_rate': 0.05
                },
                #Collector plans
                # ?????????????????????score?????????????????????????????????
                'CollectorGoToAndCollectCarbonPlan': {
                    'enabled': True
                },
                # ???????????????????????????????????????????????????????????????????????????score?????????????????????
                'CollectorGoToAndGoHomeWithCollectCarbonPlan': {
                    'enabled': True
                },
                # ????????????????????????????????????????????????????????????1????????????????????????
                'CollectorGoToAndGoHomePlan': {
                    'enabled': True
                },
                # ??????????????????????????????????????????????????????????????????????????????????????????
                'CollectorRushHomePlan': {
                    'enabled': True
                }
            },
            # ?????????????????????
            'collector_config': {
                # ????????????
                'gohomethreshold': 100,
            },
            # ????????????
            'row_count': 15,
            'column_count': 15,
            # ???????????????????????????score???????????????-inf???
            'mask_preference_index': -1e9
        }
        #?????????????????????????????????
        self.game_state = {
            'board': None,
            'observation': None,
            'configuration': None,
            'our_player': None,  #carbon.helpers.Player class from board field
            'opponent_player':
            None  #carbon.helpers.Player class from board field
        }

    #get Chebyshev distance of two positions, x mod self.config['row_count] ,y
    #mod self.config['column_count]
    def get_distance(self, x1, y1, x2, y2):
        x_1_to_2= (x1 - x2 +
                self.config['row_count']) % self.config['row_count'] 
        y_1_to_2= (
                    y1 - y2 +
                    self.config['column_count']) % self.config['column_count']
        dis_x = min(self.config['row_count'] - x_1_to_2 , x_1_to_2)
        dis_y = min(self.config['column_count'] - y_1_to_2 , y_1_to_2)
        return dis_x + dis_y

    @staticmethod
    def to_env_commands(policy_actions: Dict[str, int]) -> Dict[str, str]:
        """
        Actions output from policy convert to actions environment can accept.
        :param policy_actions: (Dict[str, int]) Policy actions which specifies the agent name and action value.
        :return env_commands: (Dict[str, str]) Commands environment can accept,
            which specifies the agent name and the command string (None is the stay command, no need to send!).
        """
        def agent_action(agent_name, command_value) -> str:
            # hack here, ???????????????????????????,???????????????????????????
            actions = BaseActions if 'recrtCenter' in agent_name else WorkerActions
            return actions[command_value].name if 0 < command_value < len(
                actions) else None

        env_commands = {}
        for agent_id, cmd_value in policy_actions.items():
            command = agent_action(agent_id, cmd_value)
            if command is not None:
                env_commands[agent_id] = command
        return env_commands

    #????????????????????????Plan
    def make_possible_plans(self):
        plans = []
        board = self.game_state['board']
        for cell_id, cell in board.cells.items():
            # iterate over all collectors planters and recrtCenter of currnet
            # player
            for collector in self.game_state['our_player'].collectors:
                plan = (CollectorGoToAndCollectCarbonPlan(
                    collector, cell, self))
                plans.append(plan)
                plan = (CollectorGoToAndGoHomeWithCollectCarbonPlan(
                    collector, cell, self))
                plans.append(plan)
                plan = (CollectorGoToAndGoHomePlan(
                    collector, cell, self))
                plans.append(plan)

                plan = (CollectorRushHomePlan(
                    collector, cell, self))
                plans.append(plan)

            for planter in self.game_state['our_player'].planters:
                plan = (PlanterRobTreePlan(
                    planter, cell, self))

                plans.append(plan)
                plan = (PlanterPlantTreePlan(
                    planter, cell, self))
                plans.append(plan)

            for recrtCenter in self.game_state['our_player'].recrtCenters:
                #TODO:?????????load?????????recrtCenterPlan???
                plan = SpawnPlanterPlan(recrtCenter, cell, self)
                plans.append(plan)
                plan = SpawnCollectorPlan(recrtCenter, cell, self)
                plans.append(plan)
            pass
        pass
        plans = [
            plan for plan in plans
            if plan.preference_index != self.config['mask_preference_index'] and plan.preference_index > 0
        ]
        return plans

    #???Board,Observation,Configuration?????????????????????PlanningPolicy???
    def parse_observation(self, observation, configuration):
        self.game_state['observation'] = observation
        self.game_state['configuration'] = configuration
        self.game_state['board'] = Board(observation, configuration)
        self.game_state['our_player'] = self.game_state['board'].players[
            self.game_state['board'].current_player_id]
        self.game_state['opponent_player'] = self.game_state['board'].players[
            1 - self.game_state['board'].current_player_id]

    #????????????Plan???????????????Agent?????????????????????Plan
    def possible_plans_to_plans(self, possible_plans: BasePlan):
        #TODO:??????plan???????????????,??????2???????????????????????????????????????????????????plan??????
        #????????????????????????
        source_agent_id_plan_dict = {}
        possible_plans = sorted(possible_plans, key=lambda x: x.preference_index, reverse=True)
        
        collector_cell_plan = dict()
        planter_cell_plan = dict()
        
        # ???????????????????????????x
        center_position = self.game_state['our_player'].recrtCenters[0].position
        collector_cell_plan[center_position] = -100

        for possible_plan in possible_plans:
            if possible_plan.source_agent.id in source_agent_id_plan_dict:
                continue
            if isinstance(possible_plan.source_agent, Collector):
                if collector_cell_plan.get(possible_plan.target.position, 0) > 0:
                    continue
                collector_cell_plan[possible_plan.target.position] = collector_cell_plan.get(possible_plan.target.position, 1)
                source_agent_id_plan_dict[
                    possible_plan.source_agent.id] = possible_plan    
            elif isinstance(possible_plan.source_agent, Planter):
                if planter_cell_plan.get(possible_plan.target.position, 0) > 0:
                    continue
                planter_cell_plan[possible_plan.target.position] = planter_cell_plan.get(possible_plan.target.position, 1)
                source_agent_id_plan_dict[
                    possible_plan.source_agent.id] = possible_plan             
            else:
                source_agent_id_plan_dict[
                    possible_plan.source_agent.id] = possible_plan
        #print(source_agent_id_plan_dict)
        #for s, t in source_agent_id_plan_dict.items():
        #    print(s, t.target.position)
        return source_agent_id_plan_dict.values()

    def calculate_carbon_contain(map_carbon_cell: Dict) -> Dict:
        """????????????????????????????????????????????????????????????????????????????????????"""
        carbon_contain_dict = dict()  # ??????????????????????????????????????????4???????????????????????????, {(0, 0): 32}
        for _loc, cell in map_carbon_cell.items():

            valid_loc = [(_loc[0], _loc[1] - 1),
                        (_loc[0] - 1, _loc[1]),
                        (_loc[0] + 1, _loc[1]),
                        (_loc[0], _loc[1] + 1)]  # ??????????????????????????????????????????

            forced_pos_valid_loc = str(valid_loc).replace('-1', '14')  # ????????????????????? 15 * 15
            forced_pos_valid_loc = eval(forced_pos_valid_loc.replace('15', '0'))

            filter_cell = \
                [_c for _, _c in map_carbon_cell.items() if getattr(_c, "position", (-100, -100)) in forced_pos_valid_loc]

            assert len(filter_cell) == 4  # ???????????????????????????????????????

            carbon_contain_dict[cell] = sum([_fc.carbon for _fc in filter_cell])

        map_carbon_sum_sorted = dict(sorted(carbon_contain_dict.items(), key=lambda x: x[1], reverse=True))

        return map_carbon_sum_sorted

    #????????????????????????
    #????????????????????????????????????
    def take_action(self, observation, configuration):
        self.global_position_mask = dict()
                            
        self.parse_observation(observation, configuration)
        possible_plans = self.make_possible_plans()
        plans = self.possible_plans_to_plans(possible_plans)

        # print(command)
        # ????????????????????????cmd??????
        # ????????????
        """
        {'player-0-recrtCenter-0': 'RECPLANTER', 'player-0-worker-0': 'RIGHT', 'player-0-worker-5': 'DOWN', 'player-0-worker-6': 'DOWN', 'player-0-worker-7': 'RIGHT', 'player-0-worker-8': 'UP', 'player-0-worker-12': 'UP', 'player-0-worker-13': 'UP'}
        """
        def remove_none_action_actions(plan_action_dict):
            return {
                k: v['action'].value
                for k, v in plan_action_dict.items() if v['action'] is not None
            }

        plan_dict = {
            plan.source_agent.id: {
                'action': plan.translate_to_action(),
                'plan': plan
            }
            for plan in plans
        }
        clean_plan_id_action_value_dict = remove_none_action_actions(plan_dict)
        command_list = self.to_env_commands(clean_plan_id_action_value_dict)
        #print(command_list)
        return command_list

my_policy = PlanningPolicy()

def agent(obs, configuration):
    global my_policy
    commands = my_policy.take_action(obs, configuration)
    return commands
