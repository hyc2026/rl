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
                             np.array((-1, 0))])  # 与WorkerActions相对应






# Plan是一个Agent行动的目标，它可以由一个Action完成(比如招募捕碳者），也可以由多
# 个Action完成（比如种树者走到一个地方去种树）
#
# 我们的方法是对每个Agent用我们设计的优先级函数选出最好的Plan，然后对每个Agent把这个Plan翻译成(当前最好的)Action
class BasePlan(ABC):
    #这里的source_agent,target都是对象，而不是字符串
    #source: 实施这个Plan的Agent: collector,planter,recrtCenter
    #target: 被实施这个Plan的对象: collector,planter,recrtCenter,cell
    def __init__(self, source_agent, target, planning_policy):
        self.source_agent = source_agent
        self.target = target
        self.planning_policy = planning_policy
        self.preference_index = None  #这个Plan的优先级因子

    #根据Plan生成Action
    @abstractmethod
    def translate_to_action(self):
        pass


# 这个类是由转化中心实施的Plans
class RecrtCenterPlan(BasePlan):
    def __init__(self, source_agent, target, planning_policy):
        super().__init__(source_agent, target, planning_policy)


# 这个Plan是指转化中心招募种树者
class RecrtCenterSpawnPlanterPlan(RecrtCenterPlan):
    def __init__(self, source_agent, target, planning_policy):
        super().__init__(source_agent, target, planning_policy)
        self.calculate_score()

    #CUSTOM:根据策略随意修改
    #计算转化中心生产种树者的优先级因子
    #当前策略是返回PlanningPolicy中设定的固定值或者一个Mask(代表关闭，值为负无穷)
    def calculate_score(self):
        #is valid
        if self.check_validity() == False:
            self.preference_index = self.planning_policy.config[
                'mask_preference_index']
        else:
            self_planters_count=self.planning_policy.game_state['our_player'].planters.__len__() 
            self_collectors_count =  self.planning_policy.game_state['our_player'].collectors.__len__() 
            self.preference_index =  self.planning_policy.config['enabled_plans']['RecrtCenterSpawnPlanterPlan']['planter_count_weight'] * self_planters_count + self.planning_policy.config['enabled_plans']['RecrtCenterSpawnPlanterPlan']['collector_count_weight'] * self_collectors_count + 1

    def check_validity(self):
        #没有开启
        if self.planning_policy.config['enabled_plans'][
                'RecrtCenterSpawnPlanterPlan']['enabled'] == False:
            return False
        #类型不对
        if not isinstance(self.source_agent, RecrtCenter):
            return False
        if not isinstance(self.target, Cell):
            return False

        #位置不对
        if self.source_agent.cell != self.target:
            return False
        #钱不够
        if self.planning_policy.game_state[
                'our_player'].cash < self.planning_policy.game_state[
                    'configuration']['recPlanterCost']:
            return False
        return True

    def translate_to_action(self):
        return RecrtCenterAction.RECPLANTER

# 这个Plan是指转化中心招募捕碳者
class RecrtCenterSpawnCollectorPlan(RecrtCenterPlan):
    def __init__(self, source_agent, target, planning_policy):
        super().__init__(source_agent, target, planning_policy)
        self.calculate_score()

    #CUSTOM:根据策略随意修改
    #计算转化中心生产种树者的优先级因子
    #当前策略是返回PlanningPolicy中设定的固定值或者一个Mask(代表关闭，值为负无穷)
    def calculate_score(self):
        #is valid
        if self.check_validity() == False:
            self.preference_index = self.planning_policy.config[
                'mask_preference_index']
        else:
            self_planters_count=self.planning_policy.game_state['our_player'].planters.__len__() 
            self_collectors_count =  self.planning_policy.game_state['our_player'].collectors.__len__() 
            self.preference_index =  self.planning_policy.config['enabled_plans']['RecrtCenterSpawnCollectorPlan']['planter_count_weight'] * self_planters_count + self.planning_policy.config['enabled_plans']['RecrtCenterSpawnCollectorPlan']['collector_count_weight'] * self_collectors_count + 1

    def check_validity(self):
        #没有开启
        if self.planning_policy.config['enabled_plans'][
                'RecrtCenterSpawnPlanterPlan']['enabled'] == False:
            return False
        #类型不对
        if not isinstance(self.source_agent, RecrtCenter):
            return False
        if not isinstance(self.target, Cell):
            return False

        #位置不对
        if self.source_agent.cell != self.target:
            return False
        #钱不够
        if self.planning_policy.game_state[
                'our_player'].cash < self.planning_policy.game_state[
                    'configuration']['recCollectorCost']:
            return False
        return True

    def translate_to_action(self):
        return RecrtCenterAction.RECCOLLECTOR

class PlanterPlan(BasePlan):
    def __init__(self, source_agent, target, planning_policy):
        super().__init__(source_agent, target, planning_policy)

    def check_valid(self):
        yes_it_is = isinstance(self.source_agent, Planter)
        return yes_it_is

    def get_actual_plant_cost(self):
        configuration = self.planning_policy.game_state['configuration']

        if len(self.planning_policy.game_state['our_player'].tree_ids) == 0:
            return configuration.recPlanterCost
        else:
            return configuration.recPlanterCost + configuration.plantCostInflationRatio * configuration.plantCostInflationBase**self.planning_policy.game_state[
                'board'].trees.__len__()

    def get_tree_absorb_carbon_speed_at_cell(self, cell: Cell):
        pass


class PlanterGoToAndPlantTreeAtTreeAtPlan(PlanterPlan):
    def __init__(self, source_agent, target, planning_policy):
        super().__init__(source_agent, target, planning_policy)
        self.calculate_score()

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

            self.preference_index = self.target.down.carbon * self.planning_policy.config[
                'enabled_plans']['PlanterGoToAndPlantTreeAtTreeAtPlan'][
                    'cell_carbon_weight'] + distance * self.planning_policy.config[
                        'enabled_plans']['PlanterGoToAndPlantTreeAtTreeAtPlan'][
                            'cell_distance_weight']

    def check_validity(self):
        #没有开启
        if self.planning_policy.config['enabled_plans'][
                'PlanterGoToAndPlantTreeAtTreeAtPlan']['enabled'] == False:
            return False
        #类型不对
        if not isinstance(self.source_agent, Planter):
            return False
        if not isinstance(self.target, Cell):
            return False
        if self.target.tree.player_id == self.source_agent.player_id:
            return False
        if self.target.tree is None:
            return False

        #钱不够
        #if self.planning_policy.game_state[
        #        'our_player'].cash < self.get_actual_plant_cost():
        #    return False
        return True

    def translate_to_action(self):
        if self.source_agent.cell == self.target and \
            self.planning_policy.global_position_mask.get(self.target.position, 0) == 0:
            self.planning_policy.global_position_mask[self.target.position] = 1
            return None
        else:
            old_position = self.source_agent.cell.position
            old_distance = self.planning_policy.get_distance(
                old_position[0], old_position[1], self.target.position[0],
                self.target.position[1])

            move_list = []
            for move in WorkerAction.moves():
                new_position = self.source_agent.cell.position + move.to_point(
                )
                new_distance = self.planning_policy.get_distance(
                    new_position[0], new_position[1], self.target.position[0],
                    self.target.position[1])
                move_list.append((move, new_distance))

            move_list = sorted(move_list, key=lambda x: x[1])

            for move, _ in move_list:
                new_position = self.source_agent.cell.position + move.to_point()
                if self.planning_policy.global_position_mask.get(new_position, 0) == 0:
                    self.planning_policy.global_position_mask[new_position] = 1
                    return move
            return None


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
        #类型不对
            if not isinstance(self.source_agent, Collector):
                return False
            if not isinstance(self.target, Cell):
                return False
            if self.target.tree is not None:
                return False
            if self.source_agent.carbon > self.planning_policy.config['collector_config']['gohomethreshold']:
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

            self.preference_index = min(self.target.carbon * (1.05 ** distance) / (distance + 1), 100)
            
    
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
        #类型不对
            if not isinstance(self.source_agent, Collector):
                return False
            if not isinstance(self.target, Cell):
                return False
            if self.target.tree is not None:
                return False
            if self.source_agent.carbon <= self.planning_policy.config['collector_config']['gohomethreshold']:
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
                self.preference_index = min(self.target.carbon * (1.05 ** source_target_distance) / (source_target_distance + 1), 100) + 100
            else:
                self.preference_index = min(self.target.carbon * (1.05 ** source_target_distance) / (source_target_distance + 1), 100) - 100

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
        #类型不对
            if not isinstance(self.source_agent, Collector):
                return False
            if not isinstance(self.target, Cell):
                return False
            if self.target.tree is not None:
                return False
            if self.source_agent.carbon <= self.planning_policy.config['collector_config']['gohomethreshold']:
                return False

            # 与转化中心距离大于1
            center_position = self.planning_policy.game_state['our_player'].recrtCenters[0].position
            source_position = self.source_agent.position
            if self.planning_policy.get_distance(
                source_position[0], source_position[1], center_position[0],
                center_position[1]) > 1:
                return False
            # target 不是转化中心
            target_position = self.target.position
            if target_position[0] != center_position[0] or target_position[1] != center_position[1]:
                return False
            
        return True

    def calculate_score(self):
        if self.check_validity() == False:
            self.preference_index = self.planning_policy.config[
                'mask_preference_index']
        else:
            self.preference_index = 200

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

class PlanningPolicy(BasePolicy):
    '''
    这个版本的机器人只能够发出两种指令:
    1. 基地招募种树者
       什么时候招募:  根据场上种树者数量、树的数量和现金三个维度进行加权判断。

    2. 种树者走到一个地方种树
       什么时候种: 一直种
       去哪种: 整张地图上碳最多的位置
    '''
    def __init__(self):
        super().__init__()
        #这里是策略的晁灿
        self.config = {
            'enabled_plans': {
                #recrtCenter plans
                'RecrtCenterSpawnPlanterPlan': {
                    'enabled': True,
                    'planter_count_weight':-8,
                    'collector_count_weight':2,
                    # 'cash_weight':2,
                    # 'constant_weight':,
                    # 'denominator_weight':
                },
                'RecrtCenterSpawnCollectorPlan': {
                    'enabled': True,
                    'planter_count_weight':8,
                    'collector_count_weight':-2,
                    # 'cash_weight':2,
                    # 'constant_weight':,
                    # 'denominator_weight':
                },
                #Planter plans
                'PlanterGoToAndPlantTreeAtTreeAtPlan': {
                    'enabled': True,
                    'cell_carbon_weight': 1,
                    'cell_distance_weight': -7
                },
                #Collector plans
                'CollectorGoToAndCollectCarbonPlan': {
                    'enabled': True
                },
                'CollectorGoToAndGoHomeWithCollectCarbonPlan': {
                    'enabled': True
                },
                'CollectorGoToAndGoHomePlan': {
                    'enabled': True
                }
            },
            'collector_config': {
                'gohomethreshold': 100,
            },
            'row_count': 15,
            'column_count': 15,
            'mask_preference_index': -1e9
        }
        #存储游戏中的状态，配置
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
            # hack here, 判断是否为转化中心,然后返回各自的命令
            actions = BaseActions if 'recrtCenter' in agent_name else WorkerActions
            return actions[command_value].name if 0 < command_value < len(
                actions) else None

        env_commands = {}
        for agent_id, cmd_value in policy_actions.items():
            command = agent_action(agent_id, cmd_value)
            if command is not None:
                env_commands[agent_id] = command
        return env_commands

    #计算出所有合法的Plan
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
            for planter in self.game_state['our_player'].planters:
                plan = (PlanterGoToAndPlantTreeAtTreeAtPlan(
                    planter, cell, self))


                
                plans.append(plan)
            for recrtCenter in self.game_state['our_player'].recrtCenters:
                #TODO:动态地load所有的recrtCenterPlan类
                plan = RecrtCenterSpawnPlanterPlan(recrtCenter, cell, self)
                plans.append(plan)
                plan = RecrtCenterSpawnCollectorPlan(recrtCenter, cell, self)
                plans.append(plan)
            pass
        pass
        plans = [
            plan for plan in plans
            if plan.preference_index != self.config['mask_preference_index'] and plan.preference_index > 0
        ]
        return plans

    #把Board,Observation,Configuration变量的信息存到PlanningPolicy中
    def parse_observation(self, observation, configuration):
        self.game_state['observation'] = observation
        self.game_state['configuration'] = configuration
        self.game_state['board'] = Board(observation, configuration)
        self.game_state['our_player'] = self.game_state['board'].players[
            self.game_state['board'].current_player_id]
        self.game_state['opponent_player'] = self.game_state['board'].players[
            1 - self.game_state['board'].current_player_id]

    #从合法的Plan中为每一个Agent选择一个最优的Plan
    def possible_plans_to_plans(self, possible_plans: BasePlan):
        #TODO:解决plan之间的冲突,比如2个种树者要去同一个地方种树，现在的plan选择
        #方式是不解决冲突
        source_agent_id_plan_dict = {}
        possible_plans = sorted(possible_plans, key=lambda x: x.preference_index, reverse=True)
        
        collector_cell_plan = dict()
        planter_cell_plan = dict()
        
        # 去转化中心都不冲突x
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

    #被上层调用的函数
    #所有规则为这个函数所调用
    def take_action(self, observation, configuration):
        self.global_position_mask = dict()
                            
        self.parse_observation(observation, configuration)
        possible_plans = self.make_possible_plans()
        plans = self.possible_plans_to_plans(possible_plans)

        # print(command)
        # 这个地方返回一个cmd字典
        # 类似这样
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
