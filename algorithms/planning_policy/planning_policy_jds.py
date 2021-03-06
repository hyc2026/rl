import random
import sys
import pdb

sys.path.append('.')
sys.path.append('..')
sys.path.append('../..')
import copy
from abc import ABC, ABCMeta, abstractmethod
from typing import Dict, OrderedDict, Tuple

from algorithms.base_policy import BasePolicy
from envs.obs_parser_xinnian import (BaseActions, ObservationParser,
                                     WorkerActions)
from zerosum_env.envs.carbon.helpers import (Board, Cell, Collector, Planter,
                                             Point, RecrtCenter,
                                             RecrtCenterAction, WorkerAction)


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
            #TODO 使用配置，而不是常数，这里肯定要改
            if self.planning_policy.game_state['our_player'].cash - 20 * len(
                    self.planning_policy.game_state['our_player'].planters
            ) * 4 + 5 * len(
                    self.planning_policy.game_state['our_player'].trees) < 30:
                self.preference_index = self.preference_index = self.planning_policy.config[
                    'mask_preference_index']
            else:
                self.preference_index = self.planning_policy.config[
                    'enabled_plans']['RecrtCenterSpawnPlanterPlan'][
                        'preference_index']

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

        if (not self.target.collector is None) or (not self.target.planter is None):
            return False
        return True

    def translate_to_action(self):
        return RecrtCenterAction.RECPLANTER

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
            #TODO 使用配置，而不是常数，这里肯定要改
            self.preference_index = self.planning_policy.config[
                'enabled_plans']['RecrtCenterSpawnPlanterPlan'][
                    'preference_index']

    def check_validity(self):
        #没有开启
        if self.planning_policy.config['enabled_plans'][
                'RecrtCenterSpawnCollectorPlan']['enabled'] == False:
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
        if (not self.target.collector is None) or (not self.target.planter is None):
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

class CollectorPlan(BasePlan):
    def __init__(self, source_agent, target, planning_policy):
        super().__init__(source_agent, target, planning_policy)

    def check_valid(self):
        yes_it_is = isinstance(self.source_agent, Collector)
        return yes_it_is

    def get_actual_plant_cost(self):
        configuration = self.planning_policy.game_state['configuration']

        return configuration.recCollectorCost

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

            adjacent_trees = 0
            if not self.target.left.tree is None:
                adjacent_trees += 1
            if not self.target.right.tree is None:
                adjacent_trees += 1
            if not self.target.up.tree is None:
                adjacent_trees += 1
            if not self.target.down.tree is None:
                adjacent_trees += 1

            if self.target.tree_id is not None:
                replace = 1
            else:
                replace = 0

            adjacent_trees = 0

            #neighbor_carbon = 0
            neighbor_carbon = self.target.left.carbon + self.target.right.carbon + self.target.down.carbon + self.target.up.carbon

            distance = self.planning_policy.get_distance(
                source_posotion[0], source_posotion[1], target_position[0],
                target_position[1])

            #if distance==0:
            #    distance = 1

            self.preference_index = neighbor_carbon * self.planning_policy.config[
                'enabled_plans']['PlanterGoToAndPlantTreeAtTreeAtPlan'][
                    'cell_carbon_weight'] / (adjacent_trees+1) * (replace * 0.5 + 1) + distance * self.planning_policy.config[
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
        if self.target.tree is not None:
            if int(self.target.tree_id.split('-')[1]) == self.source_agent.player_id:
                return False

        #钱不够
        if self.planning_policy.game_state[
                'our_player'].cash < self.get_actual_plant_cost():
            return False
        return True

    def check_collision(self, new_pos):
        # 没有开启
        # if self.planning_policy.config['enabled_plans'][
        #        'PlanterGoToAndPlantTreeAtTreeAtPlan']['enabled'] == False:
        #    return False

        if new_pos.planter is not None:
            if new_pos.planter.id == self.source_agent.player_id:
                return False

        if new_pos.collector is not None:
            return False

        return True

    def translate_to_action(self):
        if self.source_agent.cell == self.target:
            return None
        else:
            old_position = self.source_agent.cell.position
            old_distance = self.planning_policy.get_distance(
                old_position[0], old_position[1], self.target.position[0],
                self.target.position[1])
            cells = self.planning_policy.game_state['board'].cells
            for move in WorkerAction.moves():
                new_position = self.source_agent.cell.position + move.to_point(
                )
                new_position = [new_position.x, new_position.y]
                new_position[0] = (new_position[0] + 15) % 15
                new_position[1] = (new_position[1] + 15) % 15
                new_position = (new_position[0], new_position[1])

                new_distance = self.planning_policy.get_distance(
                    new_position[0], new_position[1], self.target.position[0],
                    self.target.position[1])

                if new_distance < old_distance:
                    if not self.check_collision(cells[new_position]):
                        continue
                    return move

            return move


class CollectorAtPlan(CollectorPlan):
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

            '''
            adjacent_trees = 0
            if not self.target.left.tree is None:
                adjacent_trees += 1
            if not self.target.right.tree is None:
                adjacent_trees += 1
            if not self.target.up.tree is None:
                adjacent_trees += 1
            if not self.target.down.tree is None:
                adjacent_trees += 1

            if adjacent_trees>0:
                id = self.target.tree_id
            '''

            #neighbor_carbon = 0
            neighbor_carbon = self.target.carbon

            distance = self.planning_policy.get_distance(
                source_posotion[0], source_posotion[1], target_position[0],
                target_position[1])

            if self.planning_policy.game_state['our_player'].recrtCenters[0].position==target_position:
                if self.source_agent.carbon>70:
                    self.preference_index = 500
                    return
            #    distance = 10

            self.preference_index = neighbor_carbon + distance * (-7) + (source_posotion==target_position) * 5

    def check_validity(self):
        #没有开启
        #if self.planning_policy.config['enabled_plans'][
        #        'PlanterGoToAndPlantTreeAtTreeAtPlan']['enabled'] == False:
        #    return False
        #类型不对
        if not isinstance(self.source_agent, Collector):
            return False
        if not isinstance(self.target, Cell):
            return False
        #if self.target.tree is not None:
        #    if int(self.target.tree_id.split('-')[1]) == self.source_agent.player_id:
        #        return False
        '''
        if self.target.planter is not None:
            if self.target.planter.id == self.source_agent.player_id:
                return False

        if self.target.planter is not None:
            if self.target.planter.id == self.source_agent.player_id:
                return False
        '''

        return True

    def check_collision(self, new_pos):
        # 没有开启
        # if self.planning_policy.config['enabled_plans'][
        #        'PlanterGoToAndPlantTreeAtTreeAtPlan']['enabled'] == False:
        #    return False

        if new_pos.planter is not None:
            if new_pos.planter.id == self.source_agent.player_id:
                return False

        if new_pos.collector is not None:
            if new_pos.collector.id == self.source_agent.player_id:
                return False


        return True

    def translate_to_action(self):
        if self.source_agent.cell == self.target:
            return None
        else:
            old_position = self.source_agent.cell.position
            old_distance = self.planning_policy.get_distance(
                old_position[0], old_position[1], self.target.position[0],
                self.target.position[1])

            cells = self.planning_policy.game_state['board'].cells
            for move in WorkerAction.moves():
                new_position = self.source_agent.cell.position + move.to_point(
                )
                new_position = [new_position.x, new_position.y]
                new_position[0] = (new_position[0] + 15) % 15
                new_position[1] = (new_position[1] + 15) % 15
                new_position = (new_position[0], new_position[1])

                new_distance = self.planning_policy.get_distance(
                    new_position[0], new_position[1], self.target.position[0],
                    self.target.position[1])

                if new_distance < old_distance:
                    if not self.check_collision(cells[new_position]):
                        continue
                    return move

            return None

class PlanningPolicyJDS(BasePolicy):
    '''
    这个版本的机器人只能够发出两种指令:
    1. 基地招募种树者
       什么时候招募:  有钱就招募

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
                    'preference_index': 100
                },
                'RecrtCenterSpawnCollectorPlan': {
                    'enabled': True
                },
                #Planter plans
                'PlanterGoToAndPlantTreeAtTreeAtPlan': {
                    'enabled': True,
                    'cell_carbon_weight': 1,
                    'cell_distance_weight': -5
                }
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
        dis_x = (x1 - x2 + self.config['row_count']) % self.config['row_count']
        dis_y = (y1 - y2 + self.config['column_count']) % self.config['column_count']
        return min(dis_x, self.config['row_count']-dis_x) + min(dis_y, self.config['column_count']-dis_y)

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
                plan = CollectorAtPlan(collector, cell, self)
                plans.append(plan)
            #pdb.set_trace()
            for planter in self.game_state['our_player'].planters:
                plan = (PlanterGoToAndPlantTreeAtTreeAtPlan(
                    planter, cell, self))
                plans.append(plan)
            for recrtCenter in self.game_state['our_player'].recrtCenters:
                #TODO:动态地load所有的recrtCenterPlan类
                if len(self.game_state['our_player'].planters)>2:
                    plan = RecrtCenterSpawnCollectorPlan(recrtCenter, cell, self)
                    plans.append(plan)
                else:
                    plan = RecrtCenterSpawnPlanterPlan(recrtCenter, cell, self)
                    plans.append(plan)
            pass
        pass
        plans = [
            plan for plan in plans
            if plan.preference_index != self.config['mask_preference_index']
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
        for possible_plan in possible_plans:
            if possible_plan.source_agent.id not in source_agent_id_plan_dict:
                source_agent_id_plan_dict[
                    possible_plan.source_agent.id] = possible_plan
            else:
                if source_agent_id_plan_dict[
                        possible_plan.source_agent.
                        id].preference_index < possible_plan.preference_index:
                    source_agent_id_plan_dict[
                        possible_plan.source_agent.id] = possible_plan
        return source_agent_id_plan_dict.values()

    #被上层调用的函数
    #所有规则为这个函数所调用
    def take_action(self, observation, configuration):
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
        return command_list
