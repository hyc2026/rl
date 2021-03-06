# encoding: utf-8
import copy
from abc import ABC, abstractmethod
from typing import Dict, Tuple, List, Optional
import numpy as np

from zerosum_env.envs.carbon.helpers import \
    (Board, Player, Cell, Collector, Planter, Point,
        RecrtCenter, Tree, Worker, RecrtCenterAction, WorkerAction)
from random import randint, shuffle, choice, choices

AREA_SIZE = 15
MAX_TREE_AGE = 50  # 树最大50岁
TOP_CARBON_CONTAIN = 100  # 选择 top n 的碳含量单元格
PREEMPT_BONUS = 50000  # 抢树偏好
PROTECT_BONUS = 25000  # 保护偏好
TREE_PLANTED_LIMIT = 10  # 在场树的数量大于该值，则停止种树

assert PREEMPT_BONUS > PROTECT_BONUS  # 抢优先级大于保护

BaseActions = [None, RecrtCenterAction.RECCOLLECTOR, RecrtCenterAction.RECPLANTER]

WorkerActions = [None, WorkerAction.UP, WorkerAction.RIGHT, WorkerAction.DOWN, WorkerAction.LEFT]


danger_zone: Dict[Point, int] = {}  # 保存种树员的危险区域，不能走，或者已被占领区域，每个回合更新一下
our_agent_next_posistions: Dict[Point, int] = {}

def get_distance(p1: Point, p2: Point) -> int:
    x1, y1 = p1[0], p1[1]
    x2, y2 = p2[0], p2[1]
    x_1_to_2 = (x1 - x2 + AREA_SIZE) % AREA_SIZE
    y_1_to_2 = (y1 - y2 + AREA_SIZE) % AREA_SIZE
    dis_x = min(AREA_SIZE - x_1_to_2, x_1_to_2)
    dis_y = min(AREA_SIZE - y_1_to_2, y_1_to_2)
    return dis_x + dis_y


class AgentBase:
    def __init__(self):
        pass

    def move(self, **kwargs):
        """移动行为，需要移动到什么位置"""
        pass

    @ abstractmethod
    def action(self, **kwargs):
        pass


def get_sorrounding_carbons(cell: Cell):
    """ return the carbon of up, down, left, and right of given cell
    """
    sum_carbon = 0
    sum_carbon += cell.up.carbon
    sum_carbon += cell.down.carbon
    sum_carbon += cell.left.carbon
    sum_carbon += cell.right.carbon
    return sum_carbon


def calculate_carbon_contain(map_carbon_cell: Dict, ours_info: Player, planter_target, cur_board: Board) -> Dict:
    """Rank all positions for planting trees"""
    carbon_contain_dict = dict()  # 用来存储地图上每一个位置周围4个位置当前的碳含量, {(0, 0): 32}
    our_base_position = ours_info.recrtCenters[0].position
    oppo_base_position = cur_board.opponents[0].recrtCenters[0].position

    # 排除这些cell
    excluded_positions = [our_base_position, oppo_base_position]
    # 有树，及其周围（九宫格内都不行） TODO: 这边可以做一个改变，九宫格内或许可以有树
    for _, tree in cur_board.trees.items():
        excluded_positions.extend([tree.position, tree.cell.up.position, tree.cell.down.position, tree.cell.left.position, tree.cell.right.position, tree.cell.up.left.position, tree.cell.up.right.position, tree.cell.down.left.position, tree.cell.down.right.position])

    # 已经选好的目标位置不能种树
    for _, cell in planter_target.items():
        excluded_positions.extend([cell.position])

    for _loc, cell in map_carbon_cell.items():
        # 家门口附近的不考虑
        if get_distance(_loc, our_base_position) <= int(AREA_SIZE / 2):
            continue

        good = False
        # 敌人家门口的挺好
        if get_distance(_loc, oppo_base_position) <= int(AREA_SIZE / 2):
            good = True

        if _loc in excluded_positions:  # 排除的区域里
            continue
        # TODO: 这边的权重可以进行修改
        total_carbon = get_sorrounding_carbons(cell)
        if good:  # 奖励一下，鼓励种他们家门口
            total_carbon *= 1.5
        carbon_contain_dict[cell] = total_carbon

    map_carbon_sum_sorted = dict(
        sorted(carbon_contain_dict.items(), key=lambda x: x[1], reverse=True))

    return map_carbon_sum_sorted

# 判断附近有没有敌方的种树员,  敌方距离 <= d_threshold，判断为有敌人
def has_enermy_planters(tree_pos: Point, oppo_planters: List[Planter], d_threshold=2):
    for p in oppo_planters:
        d = get_distance(tree_pos, p.position)
        if d <= d_threshold:
            return True
    return False


def get_all_enermy_workers(enermies: List[Player], worker_type: Optional[Worker]) -> List[Worker]:
    workers = []
    for player in enermies:
        for worker in player.workers:
            if worker_type:
                if isinstance(worker, worker_type):
                    workers.append(worker)
            else:
                workers.append(worker)
    return workers


def get_moved_position(src_pos: Point, move: WorkerAction):
    """从 src_pos 移动后的坐标"""
    return src_pos.translate(move.to_point(), AREA_SIZE)


def get_actual_plant_cost(configuration, board: Board, our_player: Player):
    if len(our_player.tree_ids) == 0:
        return configuration.recPlanterCost
    else:
        # 当该玩家有树的时候，种树的成本会越来越高
        # i = configuration.plantCostInflationBase  # 1.235
        # j = configuration.plantCostInflationRatio  # 5
        return configuration.recPlanterCost + configuration.plantCostInflationRatio * configuration.plantCostInflationBase ** len(board.trees)


class CollectorAct(AgentBase):
    def __init__(self):
        super().__init__()
        self.workaction = WorkerAction
        self.collector_target: Cell = None  # 这个target是敌方collector的cell 或 敌方的base cell

    def _target_plan(self, collector: Collector, ours_info, oppo_info) -> Optional[Cell]:
        return 1


    def get_safe_moves(self, collector: Collector):
        safe_moves = []
        for move in WorkerAction.moves():
            next_pos = get_moved_position(collector.position, move)
            # 不能去敌方基地，危险
            if next_pos == self.oppo_base.position and collector.carbon == 0:
                continue
            if next_pos in our_agent_next_posistions:  # 这个位置有自己人也不能去
                continue
            # 如果该位置敌方捕碳员的碳比我少也不能去
            worker = self.board[next_pos].worker
            if worker:
                if worker.is_collector and worker.player_id == self.oppo[0].id and worker.carbon < collector.carbon:
                    continue
            safe_moves.append(move)
        return safe_moves

    def get_near_enermy_cell(self, collector: Collector):
        # d <= 2 距离内的情况
        our_id = collector.player_id
        pcell: Cell = collector.cell
        check_cells: Tuple[Cell, str] = [
            (pcell.up, 'UP'), (pcell.down, 'DOWN'), (pcell.left, 'LEFT'), (pcell.right, 'RIGHT'),
            # pcell.up.left, pcell.up.right, pcell.up.up,
            # pcell.down.down, pcell.down.left, pcell.down.right,
            # pcell.left.left, pcell.right.right
        ]
        max_carbon = -1
        best_cell = None
        best_move_name = None
        for cell, move_name in check_cells:
            if cell.position == self.oppo_base.position and collector.carbon == 0:  # 不能去敌方基地
                continue
            worker = cell.worker
            if worker and worker.is_collector:
                worker_id = worker.player_id
                if worker_id != our_id:
                    if worker.carbon > max_carbon:
                        max_carbon = worker.carbon
                        best_cell = cell
                        best_move_name = move_name
        if best_move_name:
            return best_cell, best_move_name
        return None, None

    def get_nearest_agent_cell(self, oppo: Player, attacker: Collector):
        o_collectors = oppo.collectors
        if o_collectors:
            min_dis = 999999
            best_cell = None
            for col in o_collectors:
                dis = get_distance(attacker.position, col.position)
                if dis < min_dis:
                    min_dis = dis
                    best_cell = col.cell
            return best_cell
        else:
            return oppo.recrtCenters[0].cell


    def move(self, ours_info: Player, oppo_info: List[Player], attacker: Collector, **kwargs):
        move_action_dict = {}
        cur_board: Board = kwargs['cur_board']
        self.board = cur_board
        self.oppo: List[Player] = oppo_info
        self.our: Player = ours_info

        oppo_base: Cell = oppo_info[0].recrtCenters[0].cell
        self.oppo_base: Cell = oppo_base
        # 距离敌方基地的距离
        dis2oppobase = get_distance(attacker.position, oppo_base.position)

        if dis2oppobase > 4:
            self.collector_target = choice(get_cross_cells(oppo_base))
        else:
            # 检查周围 d <= 2 范围内有没有敌人
            near_enermy_cell, move_name = self.get_near_enermy_cell(attacker)
            if move_name:
                self.collector_target = near_enermy_cell
                move_action_dict[attacker.id] = move_name
                return move_action_dict

        # 考虑如何移动
        # 只要保证不伤害我方人员即可
        safe_moves = self.get_safe_moves(attacker)
        if not safe_moves:
            print('attacker: no safe moves, stay still.')
            return
        else:
            old_position = attacker.position
            target_position = self.collector_target.position
            old_distance = get_distance(old_position, target_position)
            move_name_list = []
            for move in safe_moves:
                new_position = get_moved_position(old_position, move)
                new_distance = get_distance(new_position, target_position)
                if new_distance < old_distance:
                    move_name_list.append(move.name)
            if len(move_name_list) == 0:
                move_action_dict[attacker.id] = choice(safe_moves).name
            else:
                move_action_dict[attacker.id] = choice(move_name_list)

        print(f'attacker position: {attacker.position}')
        print(f'attacker move: {move_action_dict}')
        return move_action_dict


class PlanterAct(AgentBase):
    def __init__(self):
        super().__init__()
        self.workaction = WorkerAction
        self.planter_target = dict()

    def is_enough_tree(self):
        if len(self.board.trees) > TREE_PLANTED_LIMIT:
            return True
        return False

    
    def get_rob_nearest_tree(self, planter: Planter, oppo: Player, excluded_pos: List[Point]):
        """抢离得最近的敌人的树"""
        if not oppo.trees:
            return None
        min_dis = 999999
        best_cell = None
        for tree in oppo.trees:
            tree_pos = tree.position
            if tree_pos in excluded_pos:  # 已经有人去抢了
                continue
            else:
                dis = get_distance(planter.position, tree_pos)
                if dis < min_dis:
                    min_dis = dis
                    best_cell = tree.cell
        return best_cell
    

    # TODO: 这个函数有问题，需要优化
    def _target_plan(self, planter: Planter, carbon_sort_dict: Dict, ours_info, oppo_info) -> Optional[Cell]:
        """Planter 我们的种树员，carbon_sort_dict Cell碳含量从多到少排序"""
        # TODO: oppo_info 这个地方可能是多个对手，决赛时要注意
        planter_position = planter.position
        planned_target = [tcell.position for pid, tcell in self.planter_target.items()]   # 已经包含在计划内的位置

        is_enough_tree = self.is_enough_tree()
        # 如果树达到最大值，去抢树吧
        if is_enough_tree:
            best_cell = self.get_rob_nearest_tree(planter, oppo_info, planned_target)
            if best_cell:
                return best_cell

        # 取碳排量最高的前n
        carbon_sort_dict_top_n = \
            {_v: _k for _i, (_v, _k) in enumerate(
                carbon_sort_dict.items()) if _i < TOP_CARBON_CONTAIN}  # 只选取含碳量top_n的cell来进行计算。

        """在这个范围之内，有树先抢树，没树了再种树，种树也要有个上限，种的树到达一定数量之后，开始保护树"""

        # 计算planter和他的相对距离，并且结合该位置四周碳的含量，得到一个总的得分

        max_score = -1e9
        optimal_cell_sorted = list()
        one_cell = None
        for _cell, _carbon_sum in carbon_sort_dict_top_n.items():
            target_preference_score = 0
            # 跳过 planter 当前位置
            if _cell.position == planter_position:
                continue
            planter_to_cell_distance = get_distance(planter_position, _cell.position)  # 我们希望这个距离越小越好

            if _cell.tree is None:  # 此位置没有树
                if _cell.position in planned_target:
                    continue
                if _cell.recrtCenter:
                    continue
                if is_enough_tree:
                    continue

                if not one_cell:
                    one_cell = _cell
                # TODO: 注意平衡含碳量
                # log的 max: 20左右， _carbon_sum的max最大400, 3乘表示更看重carbon_sum
                target_preference_score = np.log(
                    1 / (planter_to_cell_distance + 1e-9)) + _carbon_sum / 20

            else:  # 这个位置有树
                if not one_cell:
                    one_cell = _cell
                tree_player_id = _cell.tree.player_id
                if tree_player_id != ours_info.id:   # 是对方的树
                    target_preference_score = np.log(
                        1 / (planter_to_cell_distance + 1e-9)) + PREEMPT_BONUS  # 加一个大数，表示抢敌方树优先，抢敌方距离最近的树优先

                    if is_enough_tree:
                        target_preference_score = 1000000
                else:
                    if is_enough_tree:  # 是我方的树，并且我方树的总数量>M，那就开始保护我方的树
                        target_preference_score = np.log(1 / (planter_to_cell_distance + 1e-9)) + PROTECT_BONUS
                    else:
                        continue

            if target_preference_score > max_score:
                max_score = target_preference_score
                optimal_cell_sorted.append(_cell)  # list越往后分数越大

        optimal_cell_sorted.reverse()
        # optimal_cell_sorted = optimal_cell_sorted + choices(list(carbon_sort_dict_top_n), k=20)   # 避免最优位置全部被过滤掉，所以加了一些randdom

        if len(optimal_cell_sorted) == 0:
            if one_cell:
                best_cell = one_cell
            else:
                # 实在不行就随便选了，避免报错
                best_cell = planter.cell
        else:
            best_cell = optimal_cell_sorted[0]
        return best_cell


    def protect_or_rob_tree(self, planter: Planter, ours_info: Player, oppo_info: List[Player]):
        # 如果当前种树员站在自己的树下面，同时树的附近(d<=2)有敌方种树员,那么不动，保护树
        this_tree = planter.cell.tree
        if this_tree:
            if this_tree.player_id == ours_info.id:  # 我们自己的树
                # 判断附近 d <= 2 的格子内是否有敌方种树员
                # has_enermy_planters(tree_pos, oppo_planters)
                enermy_planters = get_all_enermy_workers(oppo_info, Planter)
                has_enmery = has_enermy_planters(this_tree.position, enermy_planters)
                if has_enmery:
                    # do not move
                    print(f'planter position: {planter.position}')
                    print('has enermy nearby, do not move.')
                    return True
                else:
                    return False
            else:  # 敌人的树，抢呗
                # 树的年龄，如果太老就不要了
                # None 啥都不做就是抢树
                if this_tree.age > 40:
                    return False
                print(f'rob tree, position: {planter.position}')
                return True
        return False

    def get_safe_moves(self, planter: Planter):
        safe_moves: List[WorkerAction] = []
        print()
        print(f'danger_zone: {danger_zone}')
        print()

        print(f'{planter.id}, current pos: {planter.position}')
        for move in WorkerAction.moves():
            new_pos = get_moved_position(planter.position, move)
            if new_pos not in danger_zone:  # 只要不在危险区域就ok
                safe_moves.append(move)
        print(f'safe moves: {str([m.name for m in safe_moves])}')
        return safe_moves

    def check_home_nearby_tree(self):
        # 遍历敌方的 tree
        base_pos = self.board.current_player.recrtCenters[0].position
        # TODO: 这里可能是多个敌人
        oppo_trees = self.board.opponents[0].trees

        # 我方所有种树员
        id2planter_dict = {}
        for p in self.board.current_player.planters:
            planterid = p.id
            id2planter_dict[planterid] = p

        # 我方种树员的目标位置
        planter_target_positions_2_pid = {}
        for pid, cell in self.planter_target.items():
            planter_target_positions_2_pid[cell.position] = pid

        for tree in oppo_trees:
            tree_pos = tree.cell.position
            dis_tree2base = get_distance(base_pos, tree_pos)
            if dis_tree2base > int(AREA_SIZE / 2):
                continue

            # 需要拔掉, 挑一个最近的种树员
            print('found enermy tree in base, rob it.')
            found = False
            min_dis = 1000000
            best_plid = None
            for planter_id, planter in id2planter_dict.items():
                dis = get_distance(planter.position, tree_pos)
                if dis < min_dis:
                    min_dis = dis
                    best_plid = planter_id
                    found = True
            if found:
                # 就算之前有任务了，这里直接覆盖
                this_planter = self.board.workers[best_plid]
                self.is_target_changeable(this_planter, tree.cell, overwrite=True)
                self.planter_target[best_plid] = tree.cell
                print(f'enermy tree pos : {tree.cell.position}, planter pos : {this_planter.position}')
                del id2planter_dict[best_plid]  # 从候选列表里剔除
            else:
                break

    def get_near_tree_cell(self, planter: Planter):
        # d <= 2 距离内的情况
        our_id = self.board.current_player_id
        pcell = planter.cell
        check_cells = [
            pcell.up, pcell.down, pcell.left, pcell.right,
            pcell.up.left, pcell.up.right, pcell.up.up,
            pcell.down.down, pcell.down.left, pcell.down.right,
            pcell.left.left, pcell.right.right
        ]
        for cell in check_cells:
            tree = cell.tree
            if tree and (not cell.planter):
                tree_pid = tree.player_id
                if tree_pid != our_id:
                    return cell
        return None

    def is_target_changeable(self, planter: Planter, new_cell: Cell, overwrite=False):
        new_pos = new_cell.position
        target2planter = {tcell.position: self.board.workers[pid] for pid, tcell in self.planter_target.items()}
        old_planter: Planter = target2planter.get(new_pos, None)
        # 1. new_cell 还没被认领
        if not old_planter:
            return True
        # 2. new_cell 已经被人认领了，且不是本人，如果本人距离更近，那么就让之前的人放弃目标，我来替他完成
        if overwrite:
            self.planter_target.pop(old_planter.id)
            return True
        
        if old_planter.id != planter.id:
            old_dis = get_distance(new_pos, old_planter.position)
            new_dis = get_distance(new_pos, planter.position)
            if new_dis < old_dis:
                self.planter_target.pop(old_planter.id)
                return True
        return False
    
    
    def is_current_pos_better(self, planter: Planter):
        """如果之前目标是去种树（target_cell是空的），且现在的位置为空，且碳含量更多，就地种树"""
        cur_pos = planter.position
        our_base = self.board.current_player.recrtCenters[0].cell
        # 如果当前很危险，赶紧跑
        if cur_pos in danger_zone:
            return False
        if get_distance(cur_pos, our_base.position) <= int(AREA_SIZE / 2):
            return False
        target_cell = self.planter_target[planter.id]
        target_cell = self.board[target_cell.position]
        if target_cell.worker or target_cell.tree or planter.cell.tree:
            return False
        if target_cell.position == cur_pos:
            return False
        old_carbon = get_sorrounding_carbons(target_cell)
        new_carbon = get_sorrounding_carbons(planter.cell)
        if new_carbon > old_carbon:
            return True
        return False
    

    def move(self, ours_info: Player, oppo_info: List[Player], **kwargs):
        #
        """
        TODO: 需要改进的地方：
        1 避免碰撞：平常走路，从基地出来
        2 抢树
        """

        # 清理出局的 worker id
        new_planter_target = {}
        for wid, cell in self.planter_target.items():
            if wid in ours_info.worker_ids:
                new_planter_target[wid] = cell
        self.planter_target: Dict[str, Cell] = new_planter_target

        move_action_dict: Dict[str, str] = {}  # 这个最后遍历一遍 planters 就行了

        """需要知道本方当前位置信息，敌方当前位置信息，地图上的碳的分布"""
        # 如果planter信息是空的，则无需执行任何操作
        if ours_info.planters == []:
            return None
        
        cur_board: Board = kwargs['cur_board']
        self.board = cur_board
        map_carbon_cell = self.board.cells
        configuration = kwargs['configuration']

        
        # TODO: 从第25步开始，一个种树员留家里防守，2个种树员去对方基地周围种树

        
        # 检查一下家门口有没有敌方的树，有的话，离得最近的种树员去拔掉
        self.check_home_nearby_tree()



        # 每一次move都先计算一次附近碳多少的分布
        carbon_sort_dict = calculate_carbon_contain(map_carbon_cell, ours_info, self.planter_target, self.board)
        
        next_flag_position = None
        for planter in ours_info.planters:
            if next_flag_position:  # 这里标记的上一个 planter 的位置
                danger_zone[next_flag_position] = 1
                our_agent_next_posistions[next_flag_position] = 1
            next_flag_position = planter.position  # 这里记录该 planter 下一个位置，默认是本身, 如果后面有移动，那么更新这个变量
            
            is_protect_or_rob_tree = self.protect_or_rob_tree(planter, ours_info, oppo_info)
            if is_protect_or_rob_tree: # 如果是目标待定状态，且当前位置有树
                # 如果当前种树员站在自己的树下面，同时树的附近(d<=2)有敌方种树员,那么不动，保护树
                # 或者是敌人的树，抢树
                continue
            
            # TODO: 判断是否全部进入抢树状态，如果是那么考虑的只有距离因素了
            
            if planter.id not in self.planter_target:   # 说明他还没有策略，要为其分配新的策略

                target_cell = self._target_plan(planter=planter, carbon_sort_dict=carbon_sort_dict,ours_info=ours_info, oppo_info=oppo_info[0])  # 返回这个智能体要去哪里的一个字典

                self.planter_target[planter.id] = target_cell  # 给它新的行动

            # 当 planter 有策略了，那么看下一步的执行情况
            # 说明他有策略，看策略是否执行完毕，执行完了移出字典，没有执行完接着执行
            target_position = self.planter_target[planter.id].position
            
            if planter.position == target_position:
                self.planter_target.pop(planter.id)

                # 这个地方是想种树
                # 判断一下有没有钱
                actual_cost = get_actual_plant_cost(configuration, self.board, ours_info)
                is_enough_tree_flag = self.is_enough_tree()
                
                if ours_info.cash < actual_cost or is_enough_tree_flag:
                    if is_enough_tree_flag:
                        print('enough tree, no more need.')
                    else:
                        print(f'cash: {ours_info.cash}, plant cost: {actual_cost}, no money.')
                    
                    # 不种树就得动起来
                    safe_moves = self.get_safe_moves(planter)
                    if not safe_moves:
                        print('no safe moves, no money, stay still.')
                        continue
                    else:
                        next_move = safe_moves[0]
                        next_flag_position = get_moved_position(planter.position, next_move)
                        planter.next_action = next_move
                        print(f'no money, go to next place.  {next_move.name}')
                        continue
                else:
                    # 有钱，那就不动，种树呗
                    print('plant tree.')
                    continue
            else:  # 没有执行完接着执行
                # 重新估计一下目标的价值
                # 1. 目标是否已经被敌方占领
                # 2. 目标成本是否已经 > 收益
                target_cell = self.board[target_position]
                old_position = planter.position
                target_position = self.planter_target[planter.id].position
                old_distance = get_distance(old_position, target_position)

                # scan target
                # target_cell   planter  

                # 看一下附近(d<=2)有没有敌方的树
                # check nearby has tree can rob
                near_tree_cell = self.get_near_tree_cell(planter)
                if near_tree_cell and self.is_target_changeable(planter, near_tree_cell):
                    self.planter_target[planter.id] = near_tree_cell
                    target_position = near_tree_cell.position
                    old_distance = get_distance(old_position, target_position)

                # TODO: 如果此时已经发现目标碳含量不到原来一半了，直接改变 target
                # 需要记录一下之前周围四个格子的碳含量
                
                # 如果之前目标是去种树（target_cell是空的），且现在的位置为空，且碳含量更多，就地种树
                # is_current_pos_better(planter)
                if self.is_current_pos_better(planter):
                    self.planter_target.pop(planter.id)
                    continue


                
                safe_moves = self.get_safe_moves(planter)

                if not safe_moves:
                    print('no safe moves, stay still.')
                    continue

                has_short_path = False
                for move in safe_moves:
                    new_position = get_moved_position(planter.position, move)
                    new_distance = get_distance(new_position, target_position)

                    if new_distance < old_distance:
                        next_flag_position = new_position
                        planter.next_action = move
                        has_short_path = True
                        break

                # 没有近路，远路也要走，保命要紧
                if not has_short_path:
                    random_from_safe_move = choice(safe_moves)

                    next_flag_position = get_moved_position(planter.position, random_from_safe_move)
                    planter.next_action = random_from_safe_move
                    
                    print(f'old_distance: {old_distance}')
                    print(f'current position: {old_position}')
                    print(f'target position: {target_position}')
                    print(f'next position: {next_flag_position}')
                    print(f'in remaining route, no shutcut, random move {random_from_safe_move.name}')
        danger_zone[next_flag_position] = 1  # 标记最后一个 planter 的 postion
        our_agent_next_posistions[next_flag_position] = 1

        
        # 最后再遍历一遍所有的 planter, 更新 move_action_dict 字典
        for planter in ours_info.planters:
            next_action = planter.next_action
            if next_action:
                move_action_dict[planter.id] = next_action.name
        
        return move_action_dict


def get_cell_carbon_after_n_step(board: Board, position: Point, n: int) -> float:
    # 计算position这个位置的碳含量在n步之后的预估数值（考虑上下左右四个位置的树的影响）
    sorrounding_zone = []
    border = AREA_SIZE - 1
    x_left = position.x - 1 if position.x > 0 else border
    x_right = position.x + 1 if position.x < border else 0
    y_up = position.y - 1 if position.y > 0 else border
    y_down = position.y + 1 if position.y < border else 0
    # 上下左右4个格子
    sorrounding_zone.append(Point(position.x, y_up))
    sorrounding_zone.append(Point(x_left, position.y))
    sorrounding_zone.append(Point(x_right, position.y))
    sorrounding_zone.append(Point(position.x, y_down))

    start = 0
    target_cell = board.cells[position]
    c = target_cell.carbon
    if n == 0:
        return c

    # position的位置有树，直接从树暴毙之后开始算
    if target_cell.tree is not None:
        start = 50 - target_cell.tree.age + 1
        if start <= n:
            c = 30.0
        else:
            return 0

    # 对于每一回合，分别计算有几颗树在吸position的碳
    for i in range(start, n):
        tree_count = 0
        for p in sorrounding_zone:
            tree = board.cells[p].tree
            # 树在危险区域内
            if tree is not None:
                # i回合后树还没暴毙
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
            # hack here, 判断是否为转化中心,然后返回各自的命令
            actions = BaseActions if 'recrtCenter' in agent_name else WorkerActions
            return actions[command_value].name if 0 < command_value < len(actions) else None

        env_commands = {}
        for agent_id, cmd_value in policy_actions.items():
            command = agent_action(agent_id, cmd_value)
            if command is not None:
                env_commands[agent_id] = command
        return env_commands


# Plan是一个Agent行动的目标，它可以由一个Action完成(比如招募捕碳者），也可以由多
# 个Action完成（比如种树者走到一个地方去种树）
#
# 我们的方法是对每个Agent用我们设计的优先级函数选出最好的Plan，然后对每个Agent把这个Plan翻译成(当前最好的)Action
class BasePlan(ABC):
    # 这里的source_agent,target都是对象，而不是字符串
    # source: 实施这个Plan的Agent: collector,planter,recrtCenter
    # target: 被实施这个Plan的对象: collector,planter,recrtCenter,cell
    def __init__(self, source_agent, target, planning_policy):
        self.source_agent = source_agent
        self.target = target
        self.planning_policy = planning_policy
        self.preference_index = None  # 这个Plan的优先级因子

        self.config = self.planning_policy.config
        self.board: Board = self.planning_policy.game_state['board']
        self.env_config = self.planning_policy.game_state['configuration']
        self.collector_danger_zone = self.planning_policy.collector_danger_zone
        self.our_player: Player = self.planning_policy.game_state['our_player']
        self.planters_count = len(self.our_player.planters)
        self.collectors_count = len(self.our_player.collectors)

    # 根据Plan生成Action

    @abstractmethod
    def translate_to_action(self):
        pass


# 这个类是由转化中心实施的Plans
class RecrtCenterPlan(BasePlan):
    def __init__(self, source_agent, target, planning_policy):
        super().__init__(source_agent, target, planning_policy)


# 这个Plan是指转化中心招募种树者
class SpawnPlanterPlan(RecrtCenterPlan):
    def __init__(self, source_agent, target, planning_policy):
        super().__init__(source_agent, target, planning_policy)
        self.calculate_score()

    # CUSTOM:根据策略随意修改
    # 计算转化中心生产种树者的优先级因子
    # 当前策略是返回PlanningPolicy中设定的固定值或者一个Mask(代表关闭，值为负无穷)
    def calculate_score(self):
        # is valid
        if self.check_validity() == False:
            self.preference_index = self.config['mask_preference_index']
        else:
            planter_count_weight = self.config['enabled_plans']['SpawnPlanterPlan']['planter_count_weight']
            collector_count_weight = self.config['enabled_plans']['SpawnPlanterPlan']['collector_count_weight']
            self.preference_index = \
                (planter_count_weight * self.planters_count
                 + collector_count_weight * self.collectors_count + 1) / 1000

    def check_validity(self):
        # 没有开启
        if self.config['enabled_plans']['SpawnPlanterPlan']['enabled'] == False:
            return False
        # 类型不对
        if not isinstance(self.source_agent, RecrtCenter):
            return False
        if not isinstance(self.target, Cell):
            return False

        # 位置不对
        if self.source_agent.cell != self.target:
            return False

        # 如果现在基地有 worker 不能招募
        base_cell = self.target
        if base_cell.worker:
            return False

        # 人口已满
        if self.planters_count + self.collectors_count >= 10:
            return False

        # 钱不够
        if self.our_player.cash < self.env_config['recPlanterCost']:
            return False
        
        # 如果 cash < 100 且 捕碳员 < 2
        if self.our_player.cash < 100 and len(self.our_player.collectors) < 2:
            return False  # 优先找捕碳员
        
        return True

    def translate_to_action(self):
        if self.source_agent.position not in self.collector_danger_zone:
            self.collector_danger_zone[self.source_agent.position] = 1
            return RecrtCenterAction.RECPLANTER
        else:
            return None

# 这个Plan是指转化中心招募捕碳者


class SpawnCollectorPlan(RecrtCenterPlan):
    def __init__(self, source_agent, target, planning_policy):
        super().__init__(source_agent, target, planning_policy)
        self.calculate_score()

    # CUSTOM:根据策略随意修改
    # 计算转化中心生产种树者的优先级因子
    # 当前策略是返回PlanningPolicy中设定的固定值或者一个Mask(代表关闭，值为负无穷)
    def calculate_score(self):
        # is valid
        if self.check_validity() == False:
            self.preference_index = self.config[
                'mask_preference_index']
        else:
            planter_count_weight = self.config['enabled_plans']['SpawnCollectorPlan']['planter_count_weight']
            collector_count_weight = self.config['enabled_plans']['SpawnCollectorPlan']['collector_count_weight']

            our: Player = self.board.current_player
            # 这里强行插入一条补丁

            if self.board.step < 5 and our.cash > 30 and len(our.planters) == 0:
                self.preference_index = 100
            else:
                self.preference_index =  \
                (planter_count_weight * self.planters_count
                 + collector_count_weight * self.collectors_count + 1) / 1000 + 0.0001

    def check_validity(self):
        # 没有开启
        if self.config['enabled_plans'][
                'SpawnCollectorPlan']['enabled'] == False:
            return False
        # 类型不对
        if not isinstance(self.source_agent, RecrtCenter):
            return False
        if not isinstance(self.target, Cell):
            return False
        # 位置不对
        if self.source_agent.cell != self.target:
            return False

        # 人口已满
        if len(self.our_player.planters) + len(self.our_player.collectors) >= 10:
            return False

        # 如果现在基地有 worker 不能招募
        base_cell = self.target
        if base_cell.worker:
            return False

        # 钱不够
        if self.our_player.cash < self.env_config['recCollectorCost']:
            return False
        return True

    def translate_to_action(self):
        if self.source_agent.position not in self.collector_danger_zone:
            self.collector_danger_zone[self.source_agent.position] = 1
            return RecrtCenterAction.RECCOLLECTOR
        else:
            return None


class CollectorPlan(BasePlan):
    def __init__(self, source_agent, target, planning_policy):
        super().__init__(source_agent, target, planning_policy)

    def check_validity(self):
        yes_it_is = isinstance(self.source_agent, Collector)
        return yes_it_is
    
    def get_total_carry_carbon(self):
        total = 0
        for c in self.our_player.collectors:
            total += c.carbon
        return total

    # 判断下一步是否安全，如果安全，那么捕碳员就会多待一会儿
    def can_action(self, next_position):
        if next_position not in self.collector_danger_zone:
            action_cell = self.board.cells[next_position]
            collectors = [action_cell.collector, action_cell.up.collector, action_cell.down.collector, action_cell.left.collector, action_cell.right.collector]

            for collector in collectors:  # 所有捕碳员都适用这套规则
                if collector is None:
                    continue
                if collector.player_id == self.source_agent.player_id:
                    continue
                if collector.carbon <= self.source_agent.carbon:  # 敌方捕碳员碳比我们的少，躲开它
                    return False
            return True
        else:
            return False

    def translate_to_action(self):
        next_action = None
        next_position = self.source_agent.position
        
        source_position = self.source_agent.position
        target_position = self.target.position
        source_target_distance = get_distance(source_position, target_position)

        potential_action_list = []

        for action in [None] + WorkerAction.moves():
            action_position = source_position
            if action:
                action_position = get_moved_position(source_position, action)
            
            if not self.can_action(action_position):
                continue

            target_action_distance = get_distance(target_position, action_position)
            source_action_distance = get_distance(source_position, action_position)

            # 新的距离-旧的距离
            delta_dis = target_action_distance + source_action_distance - source_target_distance

            potential_action_list.append((action, action_position, delta_dis, self.board.cells[action_position].carbon))

        potential_action_list = sorted(potential_action_list, key=lambda x: (-x[2], x[3]), reverse=True)
        
        # TODO: 这种存数组，然后还是二维的，最后再弄个序号取值简直太 confusing 了，得优化
        if len(potential_action_list) > 0:  # 如果多个方向都ok，那么选一个
            next_action = potential_action_list[0][0]
            next_position = potential_action_list[0][1]
            if next_action == None and target_position == next_position:
                pass
            elif next_action == None and len(potential_action_list) > 1 and potential_action_list[1][2] == 0:
                next_action = potential_action_list[1][0]
                next_position = potential_action_list[1][1]

        self.collector_danger_zone[next_position] = 1
        return next_action


class CollectorGetCarbonPlan(CollectorPlan):
    def __init__(self, source_agent, target, planning_policy):
        super().__init__(source_agent, target, planning_policy)
        self.calculate_score()

    def check_validity(self):
        if self.config['enabled_plans']['CollectorGetCarbonPlan']['enabled'] == False:
            return False
        else:
            # 类型不对
            if not isinstance(self.source_agent, Collector):
                return False
            if not isinstance(self.target, Cell):
                return False
            if self.target.tree is not None:
                return False
            if self.source_agent.carbon > self.config['collector_config']['gohomethreshold']:
                return False
            center_position = self.our_player.recrtCenters[0].position
            source_position = self.source_agent.position
            source_center_distance = get_distance(source_position, center_position)
            # 留出足够的回家步数
            if source_center_distance >= 300 - self.board.step - 4:
                return False
        return True

    def calculate_score(self):
        if self.check_validity() == False:
            self.preference_index = self.config['mask_preference_index']
        else:
            source_position = self.source_agent.position
            target_position = self.target.position
            distance = get_distance(source_position, target_position)

            self.preference_index = get_cell_carbon_after_n_step(self.board, self.target.position, distance) / (distance + 1)

    def translate_to_action(self):
        return super().translate_to_action()


class CollectorGoHomeGetCarbonPlan(CollectorPlan):
    def __init__(self, source_agent, target, planning_policy):
        super().__init__(source_agent, target, planning_policy)
        self.calculate_score()

    def check_validity(self):
        if self.config['enabled_plans']['CollectorGoHomeGetCarbonPlan']['enabled'] == False:
            return False
        else:
            # 类型不对
            if not isinstance(self.source_agent, Collector):
                return False
            if not isinstance(self.target, Cell):
                return False
            if self.target.tree is not None:
                return False
            # 未达到捕碳阈值不许回家
            if self.source_agent.carbon <= self.config['collector_config']['gohomethreshold']:
                return False
            center_position = self.our_player.recrtCenters[0].position
            source_position = self.source_agent.position
            source_center_distance = get_distance(source_position, center_position)
            # 留出足够的回家步数
            if source_center_distance >= 300 - self.board.step - 4:
                return False
        return True

    def calculate_score(self):
        if self.check_validity() == False:
            self.preference_index = self.config['mask_preference_index']
        else:
            source_position = self.source_agent.position
            target_position = self.target.position

            center_position = self.our_player.recrtCenters[0].position
            target_center_distance = get_distance(center_position, target_position)

            source_target_distance = get_distance(source_position, target_position)

            source_center_distance = get_distance(source_position, target_position)

            if target_center_distance + source_target_distance == source_center_distance:
                # 偏好走直线
                self.preference_index = \
                    get_cell_carbon_after_n_step(self.board, self.target.position, source_target_distance) / (source_target_distance + 1) + 100
            else:
                self.preference_index = \
                    get_cell_carbon_after_n_step(self.board, self.target.position, source_target_distance) / (source_target_distance + 1) - 100

    def translate_to_action(self):
        return super().translate_to_action()


class CollectorOneStepHomePlan(CollectorPlan):
    def __init__(self, source_agent, target, planning_policy):
        super().__init__(source_agent, target, planning_policy)
        self.calculate_score()

    def check_validity(self):
        if self.config['enabled_plans']['CollectorOneStepHomePlan']['enabled'] == False:
            return False
        else:
            # 类型不对
            if not isinstance(self.source_agent, Collector):
                return False
            if not isinstance(self.target, Cell):
                return False
            if self.target.tree is not None:
                return False
            # 未达到捕碳阈值不许回家
            if self.source_agent.carbon <= self.config['collector_config']['gohomethreshold']:
                return False

            # 与转化中心距离大于1
            center_position = self.our_player.recrtCenters[0].position
            source_position = self.source_agent.position
            # 距离大于一，不执行
            if get_distance(source_position, center_position) > 1:
                return False
            # target 不是转化中心
            target_position = self.target.position
            if target_position != center_position:
                return False

        return True

    def calculate_score(self):
        if self.check_validity() == False:
            self.preference_index = self.config['mask_preference_index']
        else:
            self.preference_index = 10000

    def translate_to_action(self):
        if not self.can_action(self.target.position):
            self.collector_danger_zone[self.source_agent.position] = 1
            return None
        else:
            self.collector_danger_zone[self.target.position] = 1
        for move in WorkerAction.moves():
            new_position = get_moved_position(self.source_agent.cell.position, move)
            if new_position == self.target.position:
                return move


class CollectorRushHomePlan(CollectorPlan):
    def __init__(self, source_agent, target, planning_policy):
        super().__init__(source_agent, target, planning_policy)
        self.calculate_score()

    def check_validity(self):
        if self.config['enabled_plans']['CollectorRushHomePlan']['enabled'] == False:
            return False
        else:
            # 类型不对
            if not isinstance(self.source_agent, Collector):
                return False
            if not isinstance(self.target, Cell):
                return False
            if self.target.tree is not None:
                return False

            center_position = self.our_player.recrtCenters[0].position
            source_position = self.source_agent.position
            source_center_distance = get_distance(source_position, center_position)

            if self.target.position != center_position:
                return False
            # 碳很少也没必要回来
            if self.source_agent.carbon <= 10:
                return False
            # 还没到后期呢，先不回家
            if source_center_distance < 300 - self.board.step - 10:
                return False
            if self.get_total_carry_carbon() > 3000 and self.source_agent.carbon > 500:
                return True
        return True

    def calculate_score(self):
        if self.check_validity() == False:
            self.preference_index = self.config['mask_preference_index']
        else:
            self.preference_index = 5000

    def translate_to_action(self):
        return super().translate_to_action()


class CollectorDefensePlan(CollectorPlan):
    def __init__(self, source_agent, target, planning_policy):
        super().__init__(source_agent, target, planning_policy)
        self.calculate_score()

    def check_validity(self):
        if self.config['enabled_plans']['CollectorDefensePlan']['enabled'] == False:
            return False
        else:
            # 类型不对
            if not isinstance(self.source_agent, Collector):
                return False
            if not isinstance(self.target, Cell):
                return False
            
            center_position = self.our_player.recrtCenters[0].position
            src_position = self.source_agent.position
            tgt_position = self.target.position
            
            src_center_distance = get_distance(src_position, center_position)
            src_tgt_dis = get_distance(src_position, tgt_position)

            #必须是在家附近
            if src_center_distance > 2:
                return False
            
            # 距离目标位置 d <= 2, 且目标位置有敌方种树员，或者碳多的捕碳员
            if src_tgt_dis > 2:
                return False
            
            if self.board.step > 290:
                return False
            
            worker = self.target.worker
            if not worker:
                return False
            
            our_player_id = self.our_player.id
            if worker.player_id == our_player_id:
                return False
            
            if worker.is_planter or (worker.is_collector and worker.carbon > self.source_agent.carbon):
                return True
                        
            return False

    def calculate_score(self):
        if self.check_validity() == False:
            self.preference_index = self.config['mask_preference_index']
        else:
            self.preference_index = 8000

    def translate_to_action(self):
        src_position = self.source_agent.position
        tgt_position = self.target.position
        center_position = self.our_player.recrtCenters[0].position
        old_distance = get_distance(src_position, tgt_position)

        move_candidates = []
        for action in WorkerAction.moves():
            next_position = get_moved_position(src_position, action)
            if not self.can_action(next_position):
                continue
            
            new_dis = get_distance(next_position, tgt_position)
            if new_dis < old_distance:
                move_candidates.append(action)
        
        next_position = src_position
        best_move = None
        min_home_dis = 1000000
        if len(move_candidates) == 1:
            best_move = move_candidates[0]
            next_position = get_moved_position(src_position, best_move)
        elif len(move_candidates) > 1:
            for move in move_candidates:
                t_position = get_moved_position(src_position, move)
                new_home_dis = get_distance(center_position, t_position)
                if new_home_dis < min_home_dis:
                    min_home_dis = new_home_dis
                    best_move = move
                    next_position = t_position
        
        self.collector_danger_zone[next_position] = 1
        return best_move



def set_danger_zone(danger_zone_dict, positions):
    if isinstance(positions, List):
        for pos in positions:
            assert isinstance(pos, Point)
            danger_zone_dict[pos] = 1
    elif isinstance(positions, Point):
        danger_zone_dict[positions] = 1
    else:
        raise ValueError(f'error positions {positions}')


def get_cross_positions(cell: Cell):
    """给定一个 cell，返回其上下左右四个格子的位置列表"""
    positions = [c.position for c in [cell.up, cell.down, cell.left, cell.right]]
    return positions


def get_cross_cells(cell: Cell):
    """给定一个 cell，返回其上下左右四个格子的位置列表"""
    cells = [c for c in [cell.up, cell.down, cell.left, cell.right]]
    return cells


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
        # 这里是策略的晁灿
        self.config = {
            # 表示我们的策略库中有多少可使用的策略
            'enabled_plans': {
                # 基地 招募种树员计划
                # enabled 为 true 表示运行时会考虑该策略
                # 以下plan同理
                'SpawnPlanterPlan': {
                    'enabled': True,
                    'planter_count_weight': -7,
                    'collector_count_weight': 3,
                    # 'cash_weight':2,
                    # 'constant_weight':,
                    # 'denominator_weight':
                },
                # 基地 招募捕碳员计划
                'SpawnCollectorPlan': {
                    'enabled': True,
                    'planter_count_weight': 7,
                    'collector_count_weight': -3,
                    # 'cash_weight':2,
                    # 'constant_weight':,
                    # 'denominator_weight':
                },
                # 种树员 抢树计划
                'PlanterRobTreePlan': {
                    'enabled': True,
                    'cell_carbon_weight': 1,
                    'cell_distance_weight': -7
                },
                # 种树员 种树计划
                'PlanterPlantTreePlan': {
                    'enabled': True,
                    'cell_carbon_weight': 50,  # cell碳含量所占权重
                    'cell_distance_weight': -40,  # 与目标cell距离所占权重
                    'enemy_min_distance_weight': 50,  # 与敌方worker最近距离所占权重
                    'tree_damp_rate': 0.08,  # TODO: 这个系数是代表什么？
                    'distance_damp_rate': 0.999,  # 距离越远，其实越不划算，性价比衰减率
                    'fuzzy_value': 2,
                    'carbon_growth_rate': 0.05
                },
                # Collector plans
                # 捕碳者去全地图score最高的地方采集碳的策略
                'CollectorGetCarbonPlan': {
                    'enabled': True
                },
                # 捕碳者碳携带的数量超过阈值后，打算回家，并且顺路去score高的地方采集碳
                'CollectorGoHomeGetCarbonPlan': {
                    'enabled': True
                },
                # 捕碳者碳携带的数量超过阈值并且与家距离为1，那么就直接回家
                'CollectorOneStepHomePlan': {
                    'enabled': True
                },
                # 捕碳者根据与家的距离和剩余回合数，判断是否应该立刻冲回家送碳
                'CollectorRushHomePlan': {
                    'enabled': True
                },
                # 捕碳员在家附近防守
                'CollectorDefensePlan': {
                    'enabled': True
                }
            },
            # 捕碳者相关超参
            'collector_config': {
                # 回家阈值
                'gohomethreshold': 100,
            },
            # 地图大小
            'row_count': 15,
            'column_count': 15,
            # 把执行不了的策略的score设成该值（-inf）
            'mask_preference_index': -1e9
        }
        # 存储游戏中的状态，配置
        self.game_state = {
            'board': None,
            'observation': None,
            'configuration': None,
            'our_player': None,  # carbon.helpers.Player class from board field
            'opponent_player':
            None  # carbon.helpers.Player class from board field
        }
        self.planter_act = PlanterAct()
        self.collector_act = CollectorAct()
        self.board = None
        self.attacker = None


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

    # 计算出所有合法的Plan
    def make_possible_plans(self):
        plans = []
        board = self.board
        for cell_id, cell in board.cells.items():
            # iterate over all collectors planters and recrtCenter of currnet
            # player
            for collector in self.game_state['our_player'].collectors:

                # 这里排除那些专门就行捣蛋的捕碳员
                if self.attacker and self.attacker.id == collector.id:
                    continue

                plan = (CollectorGetCarbonPlan(collector, cell, self))
                plans.append(plan)
                
                plan = (CollectorGoHomeGetCarbonPlan(collector, cell, self))
                plans.append(plan)
                
                plan = (CollectorOneStepHomePlan(collector, cell, self))
                plans.append(plan)

                plan = (CollectorRushHomePlan(collector, cell, self))
                plans.append(plan)

                plan = (CollectorDefensePlan(collector, cell, self))
                plans.append(plan)

            # for planter in self.game_state['our_player'].planters:
            #     plan = (PlanterRobTreePlan(
            #         planter, cell, self))

            #     plans.append(plan)
            #     plan = (PlanterPlantTreePlan(
            #         planter, cell, self))
            #     plans.append(plan)

            for recrtCenter in self.game_state['our_player'].recrtCenters:
                # TODO:动态地load所有的recrtCenterPlan类
                plan = SpawnPlanterPlan(recrtCenter, cell, self)
                plans.append(plan)
                plan = SpawnCollectorPlan(recrtCenter, cell, self)
                plans.append(plan)
        plans = [
            plan for plan in plans
            if plan.preference_index != self.config['mask_preference_index'] and plan.preference_index > 0
        ]
        return plans

    # 把Board,Observation,Configuration变量的信息存到PlanningPolicy中
    def parse_observation(self, observation, configuration):
        self.game_state['observation'] = observation
        self.game_state['configuration'] = configuration
        self.game_state['board'] = Board(observation, configuration)
        self.game_state['our_player'] = self.game_state['board'].players[self.game_state['board'].current_player_id]
        self.game_state['opponent_player'] = self.game_state['board'].players[1 -
                                                                              self.game_state['board'].current_player_id]

    # 从合法的Plan中为每一个Agent选择一个最优的Plan
    def possible_plans_to_plans(self, possible_plans: BasePlan):
        source_agent_id_plan_dict = {}
        possible_plans = sorted(
            possible_plans, key=lambda x: x.preference_index, reverse=True)

        collector_cell_plan = dict()
        planter_cell_plan = dict()

        # 去转化中心都不冲突x
        center_position = self.game_state['our_player'].recrtCenters[0].position
        collector_cell_plan[center_position] = -100

        for possible_plan in possible_plans:
            if possible_plan.source_agent.id in source_agent_id_plan_dict:
                continue
            if isinstance(possible_plan.source_agent, Collector):
                # 说明已经进来过
                if collector_cell_plan.get(possible_plan.target.position, 0) > 0:
                    continue
                collector_cell_plan[possible_plan.target.position] = collector_cell_plan.get(
                    possible_plan.target.position, 1)
                source_agent_id_plan_dict[possible_plan.source_agent.id] = possible_plan
            # Planter 的计划不在这里实现
            elif isinstance(possible_plan.source_agent, Planter):
                pass
            else:
                source_agent_id_plan_dict[possible_plan.source_agent.id] = possible_plan
        return source_agent_id_plan_dict.values()

    def calculate_carbon_contain(map_carbon_cell: Dict) -> Dict:
        """遍历地图上每一个位置，附近碳最多的位置按从多到少进行排序"""
        carbon_contain_dict = dict()  # 用来存储地图上每一个位置周围4个位置当前的碳含量, {(0, 0): 32}
        for _loc, cell in map_carbon_cell.items():

            valid_loc = [(_loc[0], _loc[1] - 1),
                         (_loc[0] - 1, _loc[1]),
                         (_loc[0] + 1, _loc[1]),
                         (_loc[0], _loc[1] + 1)]  # 四个位置，按行遍历时从小到大

            forced_pos_valid_loc = str(valid_loc).replace(
                '-1', '14')  # 因为棋盘大小是 15 * 15
            forced_pos_valid_loc = eval(
                forced_pos_valid_loc.replace('15', '0'))

            filter_cell = \
                [_c for _, _c in map_carbon_cell.items() if getattr(
                    _c, "position", (-100, -100)) in forced_pos_valid_loc]

            assert len(filter_cell) == 4  # 因为选取周围四个值来吸收碳

            carbon_contain_dict[cell] = sum(
                [_fc.carbon for _fc in filter_cell])

        map_carbon_sum_sorted = dict(
            sorted(carbon_contain_dict.items(), key=lambda x: x[1], reverse=True))

        return map_carbon_sum_sorted

    def plan2dict(self, plans: List[BasePlan]):
        agent_id_2_action_number = {}
        for plan in plans:
            agent_id = plan.source_agent.id


            action = plan.translate_to_action()
            if action:
                agent_id_2_action_number[agent_id] = action.value
                if 'recrtCenter' not in agent_id:
                    next_pos = get_moved_position(plan.source_agent.position, action)
                    danger_zone[next_pos] = 1
                    our_agent_next_posistions[next_pos] = 1
            else:
                agent_id_2_action_number[agent_id] = 0
                if 'recrtCenter' not in agent_id:
                    danger_zone[plan.source_agent.position] = 1
                    our_agent_next_posistions[plan.source_agent.position] = 1
        return agent_id_2_action_number

    def set_attacker(self, our: Player):
        # 如果之前指定了 attacker
        collectors = our.collectors
        if not collectors:
            self.attacker = None
            return None

        if self.attacker:
            # 判断其是否还活着
            attacker_id = self.attacker.id
            if attacker_id in our.worker_ids:
                for col in collectors:
                    if attacker_id == col.id:
                        self.attacker = col  # 更新 obj

                if self.attacker.carbon == 0:
                    return self.attacker
            else:
                for col in collectors:
                    if col.carbon == 0:
                        self.attacker = col
                        return col
                self.attacker = None

        for col in collectors:
            if col.carbon == 0:
                self.attacker = col
                return col
        self.attacker = None
        return None

    # 被上层调用的函数
    # 所有规则为这个函数所调用
    def take_action(self, observation, configuration):
        # mask = 1 的位置不能再走了
        self.collector_danger_zone: Dict[Point, int] = {}

        self.parse_observation(observation, configuration)
        cur_board: Board = self.game_state['board']
        self.board = cur_board
        
        ours, oppo = self.board.current_player, self.board.opponents

        # print(f'################ player id: {ours.id}')
        
        if self.board.step > 10 and len(ours.workers) == 10 and ours.cash > 500:
            self.config['collector_config']['gohomethreshold'] = 2000
        else:
            self.config['collector_config']['gohomethreshold'] = 100

        # 挑出1个捕碳员作为捣蛋鬼，为其设置标记，每次先判断其是否存活
        # 如果活着，那很好；如果没了，那看看当前有没有0碳员，有的话派出一个，指定它，没有就等待

        attacker = self.set_attacker(ours)

        if ours.cash < 60 and len(ours.workers) < 4 and self.board.step < 20:
            self.attacker = None
            attacker = None

        global danger_zone, our_agent_next_posistions
        danger_zone = {}
        our_agent_next_posistions = {}

        possible_plans = self.make_possible_plans()
        plans = self.possible_plans_to_plans(possible_plans)

        agent_id_2_action_number = self.plan2dict(plans)

        # print('plans')
        # print(plans)

        # 标记敌方 collector 的周围 cell, 至于敌方的planter就不管了
        for col in oppo[0].collectors:
            cell_pos_list = [col.position] + get_cross_positions(col.cell)
            set_danger_zone(danger_zone, cell_pos_list)

        # 种树员的策略从这里开始吧，独立出来
        # 种树员做决策去哪里种树

        print()
        print(f'after collector move ...')
        print(f'danger_zone: {danger_zone}')
        print()

        planter_dict = self.planter_act.move(
            ours_info=ours,
            oppo_info=oppo,
            step=self.board.step,
            cur_board=self.board,
            configuration=configuration
        )

        attacker_dict = None
        if self.attacker:
            attacker_dict = self.collector_act.move(
                ours_info=ours,
                oppo_info=oppo,
                attacker=self.attacker,
                map_carbon_location=self.board.cells,
                step=self.board.step,
                cur_board=self.board,
                configuration=configuration
            )

        

        """
        agent_id_2_action_number:
        {'player-0-worker-0': 1, 'player-0-worker-3': 2, 'player-0-worker-5': 4, 'player-0-worker-7': 4, 'player-0-worker-9': 3, 'player-0-recrtCenter-0': 2}
        """
        
        # 我觉得在这个地方统一遍历一遍我方所有的 agents，拿到其 next action就行
        
        command_list = self.to_env_commands(agent_id_2_action_number)
        if planter_dict:
            command_list.update(planter_dict)
        # attacker_dict
        if attacker_dict:
            command_list.update(attacker_dict)

        print(f'\n\n---------step: [ {self.board.step + 2} ]')
        pos2move = {}
        for wk in ours.workers:
            wid = wk.id
            pos = wk.position
            if wid in command_list:
                pos2move[pos] = command_list[wid]
            else:
                pos2move[pos] = 'None'
        print(pos2move)
        # 这个地方返回一个cmd字典
        # 类似这样
        """
        {'player-0-recrtCenter-0': 'RECPLANTER', 'player-0-worker-0': 'RIGHT', 'player-0-worker-5': 'DOWN', 'player-0-worker-6': 'DOWN', 'player-0-worker-7': 'RIGHT', 'player-0-worker-8': 'UP', 'player-0-worker-12': 'UP', 'player-0-worker-13': 'UP'}
        """

        return command_list
