"""Build a reviewed, observable G8 skill standard and preview on a SQLite copy.

No model calls. Explicit point operations can be linked; opaque answers and
ambiguous multi-operation points retain only section evidence. This tool never
uses the old cluster label as evidence for a new skill.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import re
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build_release_v3 import build_release, build_vocabulary
from question_bank.knowledge_graph_release.contracts import KnowledgeGraphRelease, compute_content_hash, stable_record_hash
from question_bank.knowledge_graph_release.validation import validate_release

CATALOG = ROOT / 'question_bank/taxonomy/catalogs'
RELEASE = 'kgr_bnu_math_curriculum_2026_09_v6'
SOURCE = 'g8_observable_operations_20260919'
REFERENCE = 'question_bank/taxonomy/catalogs/teaching_skills_g8_upper_v6.json'

# Each rule demands a concrete operation. These are conservative selectors for
# this repair, not a runtime classifier or a clustering vocabulary.
# section, local id, name, evidence boundary, exclusion, explicit operation
SPECS = [
('1_1',106,'用线段的和差关系列式求长度','说明共线线段的包含或拼接关系，用整体减部分或分段相加求长。','不含未确认图形关系的任意加减计算。',r'由.{0,12}[＝=].{0,12}求得.{0,6}[＝=].{0,6}[−﹣-]'),
('2_3',109,'在根式运算中正确去括号和处理符号','按分配律去括号，处理括号前负号，指出并修正漏变号。','不含只给出化简结果却看不到符号处理步骤。',r'去括号|漏.{0,4}变号|没有变号'),
('4_1',204,'列表描点并连成给定函数图象','按给定函数求对应值、描点，注意分段端点和连线方式。','不凭少数点将曲线错误地连成直线。',r'顺次连线|描出.{0,8}端点并连线|填表并作图|V形|折线图象'),
('4_1',205,'由函数图象的变化量求速度或单价','计算某一区段纵横坐标的变化量之比，解释速度、单价等含义。','不把全程平均变化率当作某个区段的变化率。',r'求速度|每分钟.{0,5}元|每分.{0,5}元|变化率'),
('5_6',201,'按整除和余数条件逐步筛选整数','从一个条件列候选，依次检验其余条件，确定最小或全部整数解。','不含一般方程组消元。',r'逐步.{0,8}(筛|确定)|余数条件|公倍数'),
('6_2',203,'判断箱线图能支持的统计结论','读取五数概括、比较位置和四分位距，区分不能唯一求出的平均数和方差。','不能仅由箱线图断言精确方差或必然的方差大小。',r'箱线图.{0,10}结论|箱宽|五数概括'),
('6_1',205,'用最大值减最小值求极差','找出数据最大值和最小值，相减并比较波动范围。','极差不能替代方差，不能只从箱线图推出精确方差。',r'极差'),
('6_1',206,'按比例或频数计算加权平均数','辨认每项权重，先加权求和，再除以权重总和。','不含排序求中位数，也不把权重当作得分。',r'加权平均|权重|综合成绩'),
('5_3',203,'从拼接图的边长关系列方程求长度','以小图形边长为未知量，对照拼接后的总长、总宽列式求解。','不含只用面积公式计算已知边长的图形。',r'拼接.{0,10}方程|拼图.{0,10}方程|长宽.{0,10}方程'),
('4_3',209,'由直线平行确定一次函数斜率','用两条不同直线平行时斜率相等求参数，注意截距不能相同。','不含一般图象升降判断。',r'平行.{0,8}斜率|斜率.{0,8}相等'),
('7_2',203,'为反证法写出结论的否定','完整写出与待证结论相反的所有情形，含边界情况。','不含直接重复题设或只否定部分情形。',r'反证法|假设结论不成立'),
('7_2',204,'区分命题与提问或操作语句','判断语句是否对某事作出可判真假的判断。','不含判断一个已确定命题的真假。',r'是否是命题|不属于命题|命题必须'),
('5_1',205,'按未知数个数和次数识别二元一次方程组','检查两个未知数、每项次数为一及方程间共同未知数。','不含求方程组的数值解。',r'二元一次方程组.{0,8}(定义|识别)|判断.{0,8}二元一次方程组'),
('4_3',208,'利用点在直线上求参数或系数组合','把一个已知点代入直线式，求参数或目标系数的组合。','不含仅代入已知函数求函数值。',r'点在直线上.{0,8}得|代入.{0,8}求得[akb]|整体.{0,8}[kb]'),
('4_4',209,'根据函数图象交点和高低解方程或不等式','用交点的横坐标表示等式的解，用图象上下关系确定不等式范围。','不把交点纵坐标当作一元方程的解。',r'不等式.{0,8}解集|图象.{0,8}解不等式|交点横坐标|图象法解一元'),
('5_1',204,'把已知解代入方程求参数','用已知的一对数代入含参数的方程，求出参数值。','不含未知解的消元，也不含只检验对错。',r'把含.{0,3}的解代入|代入.{0,8}求参数|已知解.{0,8}参数'),
('3_2',207,'用坐标轴上的点的特征求坐标或参数','在x轴上纵坐标为零，在y轴上横坐标为零，据此求参数。','不含图形轴对称或到轴距离。',r'在[xy]轴上.{0,12}(坐标|参数)|[横纵]坐标为0'),
('3_2',208,'根据已知点建立坐标系并定位','利用已知点确定原点、方向和单位长度，再读出位置。','不含几何约束下求未知点。',r'确定原点|建立坐标系|定位原点'),
('3_3',205,'求关于水平或竖直直线对称的坐标','保持一个坐标不变，利用对称轴平分对应点连线求另一个坐标。','不含旋转和仅比较图形大小。',r'关于直线[xy].{0,12}对称|竖直线对称|水平线对称'),
('4_3',207,'根据一次函数中的重复图形推导坐标或边长规律','从相邻图形关系建立递推，求指定序号或通项。','不含一般函数代入或一次平移。',r'第n个|通项|递推'),
('4_4',206,'结合几何条件求直线的函数表达式','先用几何关系确定直线上的点，再求函数系数。','不等同于已给定两点坐标的单步待定系数。',r'旋转.{0,12}(表达式|解析式)|几何条件.{0,12}(表达式|解析式)'),
('4_4',207,'按几何约束分类求函数图象上的动点','用点在直线上及长度、直角或等腰等条件列式，枚举合法位置。','不含只将给定x值代入求y。',r'分类求.{0,8}[点坐标]|分类求PA=PB|情形[一二三].{0,8}斜边'),
('4_4',208,'由两组实际数据建立一次函数并预测','用两组对应数据建模，再预测新输入或反求输入量。','不含只有一次代入的现成公式求值。',r'根据两组.{0,8}数据|待定系数.{0,12}预测'),
('5_2',206,'用整体加减求二元方程中的组合量','对方程整体倍乘、相加减，直接求x+y等目标组合。','不含常规分别求两个未知数。',r'整体代入|整体相[加减]|整体法|整体加减'),
('5_4',201,'在两直线交点与方程组的解之间转换','把交点横纵坐标作为两方程公共解，反向解释图象。','不含把单条直线与x轴交点误当二元方程组解。',r'交点.{0,12}方程组.{0,4}解|方程组.{0,8}交点'),
('7_2',202,'检验命题条件并判断真假','检查结论所需条件，辨别定理、逆命题及缺条件的表述。','仅凭图形观感不能作为命题为真的依据。',r'命题.{0,8}真假|真命题|假命题'),
('3_1',201,'用有序数对或方位描述位置','根据行列顺序、参照方向和距离表示位置。','不含平面直角坐标系中的几何求点。',r'有序数对|方向角.{0,12}距离|方位.{0,8}位置'),
('3_2',201,'在坐标系中读点、描点','根据两坐标轴读出坐标，或按给定坐标标点。','不含通过几何关系推算未知点。',r'描出.{0,15}点|描点|读出.{0,8}坐标'),
('3_2',202,'由坐标求点到坐标轴的距离','辨认到哪条轴的距离，并取相应坐标绝对值。','不含两点间距离或到斜线距离。',r'到[xyｘｙ]轴.{0,8}距离|到坐标轴.{0,8}距离'),
('3_2',203,'用坐标符号判断象限或参数范围','根据横纵坐标正负确定象限，或反推参数不等式。','不含一次函数整条直线经过的象限。',r'点.{0,15}象限|横.{0,8}纵.{0,8}符号'),
('3_2',204,'由坐标差求两点间距离','把坐标差转成水平、竖直距离，必要时用勾股定理。','不含题设自定义的折线距离。',r'两点间距离|两点距离|距离公式|横坐标.{0,8}之差|纵坐标.{0,8}之差'),
('3_2',205,'由端点坐标求中点坐标','分别平均横、纵坐标得到线段中点。','不含仅已知中点的几何证明。',r'中点.{0,8}坐标|坐标.{0,8}中点'),
('3_2',206,'根据坐标变化规律求指定点','识别重复周期或递推关系并计算指定序号的坐标。','不含一般数列规律和一次平移。',r'周期.{0,12}坐标|坐标.{0,12}周期|坐标.{0,12}规律'),
('3_3',201,'求关于坐标轴或原点对称的点','依据对称轴或对称中心改变相应坐标符号。','不含图形平移、直线函数表达式变换。',r'对称点|关于[xyｘｙ]轴.{0,18}对称|关于原点.{0,12}对称|写出.{0,12}[A-Z][′₁]与'),
('3_3',202,'由平移方向和距离求坐标','按横纵方向增减坐标，或由坐标差确定平移方式。','不含轴对称和直线解析式平移。',r'平移.{0,16}坐标|坐标.{0,16}平移|向[左右上下].{0,8}单位'),
('3_3',203,'用割补或底高关系求坐标图形面积','根据坐标求底和高，或用外接矩形割补算面积。','不含根据指定面积反求动点位置。',r'割补.{0,12}面积|外接矩形|梯形减|求出.{0,12}面积|算得.{0,12}面积'),
('3_3',204,'按面积条件列式确定动点位置','设动点坐标，列底高或割补面积方程，并区分位置。','不含只计算已知图形面积。',r'面积.{0,8}方程|面积.{0,8}列.{0,8}方程|列出面积关系'),
('4_1',201,'根据式子和实际情境确定自变量范围','同时检查代数式有意义及实际数量的取值限制。','不含求某一个自变量值。',r'自变量.{0,8}取值范围|确定.{0,8}定义域'),
('4_1',202,'在函数关系中代入求值或反求自变量','代入一个变量求另一个变量，写明对应值。','不含求未知函数系数和待定系数法。',r'函数值|代入[xyｘｙ].{0,12}得|确定自变量|代入得.{0,8}(元|cm|米)'),
('4_1',203,'从函数图象读取时间、距离或变化信息','辨认坐标轴、端点和区段，读取具体量或变化过程。','不含仅根据公式求值。',r'读出.{0,15}(分钟|截距|最小值|最大值)|由图象.{0,12}(时间|距离|速度)|读图'),
('4_3',201,'用两点描出一次函数图象','取两个不同点并连成正确的直线，注明范围。','不含V形、分段折线或其他函数图象。',r'画出一次函数图象|连成直线|直线段连接|一次函数.{0,8}描点'),
('4_3',202,'用系数符号判断直线位置','根据斜率和截距正负确定直线经过的象限或图象。','不含单个点象限。',r'斜率.{0,8}符号|[kb].{0,6}正负|直线.{0,8}象限'),
('4_3',203,'判断一次函数增减性并比较函数值','用斜率正负或图象升降判断增减及同一函数值大小。','不含两个收费方案的优劣比较。',r'判断增减|增减性|随.{0,4}增大而|比较.{0,8}函数值'),
('4_3',204,'令一个坐标为零求直线与坐标轴交点','分别令x或y为零，求交点和截距。','不含两条非坐标轴直线的交点。',r'与[xy]轴.{0,6}交点|与坐标轴.{0,6}交点|令[xy]＝?=？?0|令[xy]=0'),
('4_3',205,'根据图象平移或对称变换函数式','由图象的平移或关于坐标轴对称调整一次函数系数。','不含仅求点的平移对称坐标或旋转综合。',r'上移.{0,12}截距|下移.{0,12}截距|平移.{0,15}(解析式|函数式)|关于[xy]轴对称得y'),
('4_3',206,'用点的坐标确定一次函数系数','设y=kx+b并代入已知点求k、b，写出表达式。','不把尚未推出坐标的几何综合题直接等同于本技能。',r'待定系数|求得斜率|求得截距|关于k.{0,8}b.{0,8}方程|设y=kx|设一次函数解析式|求出.{0,12}(函数|直线).{0,6}(解析式|表达式)'),
('4_4',201,'把实际数量关系列成一次函数','说明变量含义，按固定量和单位变化量列函数式。','不含只代入已有式子求一个值。',r'写出利润|建立.{0,8}函数|列出.{0,8}函数关系|写出.{0,8}费用.{0,8}函数|写出一次式'),
('4_4',202,'按分段计费条件列式和求值','先确定所处价格区间，再计算累计费用或反求用量。','不含无分段规则的代入计算。',r'后段.{0,12}解析式|分段.{0,12}(收费|计费|函数)|[梯阶]度计价'),
('4_4',203,'比较一次函数方案并求临界条件','比较两个函数值，求相等的临界点，并给出方案范围。','不含没有比较依据的单一选择结论。',r'结合图象比较费用|比较.{0,10}(方案|费用)|临界|更[省优]惠|选择方案'),
('4_4',204,'联立函数求交点或相遇时刻','把两个函数值相等列成方程，求交点或相遇时刻。','不含求与坐标轴交点和任意方程组。',r'联立.{0,15}(函数|直线)|两.{0,4}直线.{0,8}交点|相遇.{0,12}(时间|时刻)|求出四个交点'),
('4_4',205,'用底高或割补计算直线围成的面积','由截距、坐标差确定底高并求直线围成图形面积。','不含仅判断平行或求函数系数。',r'面积|以.{0,8}为底'),
('5_1',201,'检验有序数对是否为方程组的解','将两个数同时代入所有方程，检查是否都成立。','不含通过消元求未知解。',r'检验.{0,10}方程组|代入.{0,10}都成立|是否.{0,8}方程组的解'),
('5_1',202,'用一个未知数表示另一个未知数','对二元一次方程移项、除以系数，写成等价表达式。','不含把已知数值回代求值。',r'用含.{0,4}的式子表示|变形得[xy]|解出[xy]=|写成[xy]='),
('5_1',203,'按整数约束列举并筛选方程的解','根据正整数、非负整数等条件列举候选并排除不合项。','不含不带整数条件的常规消元。',r'正整数|非负整数|正约数'),
('5_2',201,'代入等价表达式消去一个未知数','先表示一个未知数，再代入另一个方程得到一元方程。','不含仅把一个数回代，也不含函数代入求值。',r'代入消元|代入.{0,12}消元|代入.{0,12}一元一次|把[②③].{0,8}代入[①②]'),
('5_2',202,'把方程倍乘后相加减消元','统一某未知数系数，再正确相加减消去该未知数。','不含只有最终数值而没有消元步骤。',r'加减消元|两式相[加减]|两方程相[加减]|倍乘|①[+＋－−-]②|②[－−-]①|消去[xy].{0,12}得'),
('5_2',203,'回代求另一未知数并写完整解','把已求得的未知数代回原方程，求另一未知数并配对。','不含把含未知数的式子代入以消元。',r'回代|代回.{0,12}求|把[xy][=＝].{0,8}代回|将[xy][＝=].{0,8}代入方程'),
('5_2',204,'把含分母或括号的方程化为标准形式','去分母、去括号、移项合并，保持方程等价。','不含独立的数值混合计算。',r'去分母|去括号|整理成.{0,18}[xy].{0,18}[xy]'),
('5_2',205,'解二元一次方程组并给出成对结果','独立完成二元一次方程组求解，最终答案为一对数。','结果型证据不证明采用了哪一种消元方法。',r'写出方程组的解|求解方程组|解方程组'),
('5_3',201,'找出两个等量关系列二元一次方程组','定义两个未知量，根据题意列两个独立等量关系。','不含只求解已给出的方程组。',r'列.{0,8}方程组|按.{0,12}列方程|设.{0,10}未知数'),
('5_3',202,'把方程组结果解释为实际方案并检验','解释两个解的实际含义，检查非负性、单位和题设限制。','不含不加解释的纯代数结果。',r'检验.{0,12}(题意|实际)|写出.{0,8}(购买|安排)方案|答.{0,8}(单价|人数)'),
('6_1',201,'计算一组数据的平均数','求总和并除以数据个数，每个观测值计一次。','不含中位数和给定权重的加权平均；比较平均数归统计决策。',r'求.{0,8}平均数|计算.{0,8}平均数|加权平均|写出平均数'),
('6_1',202,'按离差平方计算方差','先求平均数，计算每个离差平方再求平均。','不含只比较已知方差。',r'求.{0,8}方差|计算.{0,8}方差|离差平方和'),
('6_1',203,'比较方差判断数据稳定性','联系方差大小与波动大小，给出稳定性判断。','不把稳定性误当平均水平高低。',r'比较.{0,8}方差|比方差|方差.{0,8}(更小|决策|稳定)|方差比较稳定'),
('6_1',204,'按出现次数确定众数','统计频数，指出出现次数最多的值，允许并列。','不含平均数或中位数。',r'众数'),
('6_2',201,'排序并按位置求中位数','先排序，按个数奇偶取中间一个或两个值的平均。','不含对全部数据求平均。',r'中位数|排序.{0,8}中间|中间两数平均'),
('6_2',202,'读取或计算四分位数并表示箱线图','按数据分位位置确定四分位数，绘制或读取箱线图。','不含只求众数。',r'四分位|箱线图'),
('6_3',201,'根据统计量比较群体并说明选择理由','结合平均水平、典型值、稳定性或达标率作出有据选择。','只写甲、乙等结果不能单独证明比较依据。',r'比较平均|比较.{0,8}统计量|结合.{0,8}(中位数|平均数)|优秀率.{0,8}[>＞]'),
('6_3',202,'从统计图表计算人数、总数和比例','根据频数、百分比和总量之间的关系补足信息。','不把样本人数直接当作总体人数。',r'人数|样本容量|总人数|合格率|优秀率|样本.{0,6}比例|由.{0,6}人占'),
('6_3',203,'用样本比例估计总体数量','计算样本比例，再乘总体数量并说明是估计。','不含仅计算样本内人数。',r'估计总体|用样本.{0,8}估计|估计.{0,8}人'),
('7_2',201,'区分命题的条件和结论并举反例','明确已知与求证，判断真假时给出满足条件的反例。','不含单纯计算角度。',r'反例|题设.{0,6}结论|已知.{0,6}求证'),
('7_3',201,'由角的等量关系证明两直线平行','指明对应角关系及平行判定依据，形成推理链。','不含从平行线反推角关系。',r'从而.{0,8}∥|得.{0,8}∥|证.{0,8}∥|判定.{0,8}平行'),
('7_3',202,'由平行线推出角相等或互补','明确平行条件，使用同位角、内错角或同旁内角性质。','不含由角关系判定平行。',r'由.{0,8}∥.{0,8}得∠|平行得|两直线平行.{0,12}角|平行线.{0,8}性质'),
('7_3',203,'结合角平分线和角和关系列式求角','根据平分、补角或三角形内角和列等量式求未知角。','不含没有依据的最终角度数值。',r'内角和|角平分|平分.{0,8}角|互补.{0,8}得|邻补角'),
('7_3',204,'作辅助平行线连接角关系','明确辅助线与已知直线平行，把分离角关系联结起来。','不含不说明用途的任意作图。',r'过.{0,8}作.{0,8}∥|过.{0,8}作平行线'),
]


def standard():
    return {'purpose': '八上可观察技能标准；判定点归属必须有操作依据，名称不能替代题目证据。',
            'skills': [{'id': f'sk_bnu24_math_g8_upper_{section}_{number}',
                        'section_key': f'kp_bnu24_math_g8_upper_{section}', 'name': name,
                        'include': include, 'exclude': exclude, 'examples': [include],
                        'repair_pattern': pattern}
                       for section, number, name, include, exclude, pattern in SPECS]}


def build_v6():
    old = json.loads((CATALOG / 'knowledge_graph_release_v5.json').read_text(encoding='utf-8'))
    vocab = json.loads((CATALOG / 'tag_vocabulary_v6.json').read_text(encoding='utf-8'))
    nodes = {n['stable_key']: n for n in old['core_nodes']}
    parents = {r['source_key']: r['target_key'] for r in old['relations'] if r['relation_type'] == 'parent'}
    retired = {key for key, node in nodes.items() if node['status'] == 'active'
               and key.startswith('sk_bnu24_math_g8_upper_')
               and (len(key.rsplit('_', 1)[-1]) == 2 or key == 'sk_bnu24_math_g8_upper_3_3_101')}
    skills = [{**s, 'chapter_key': s['section_key'].rsplit('_', 1)[0], 'count': 0, 'aliases': [],
               'sample_targets': s['examples'], 'display_name': f"{nodes[s['section_key']]['display_name']}｜技能·{s['name']}"}
              for s in standard()['skills']]
    names = {s['id']: s['display_name'] for s in skills}
    vocabulary = build_vocabulary(vocab, skills, nodes)
    payload = build_release(old, skills, nodes, names)
    payload.update(release_id=RELEASE, taxonomy_revision=8, predecessor_release_id=old['release_id'])
    vocabulary['revision'] = 8
    definitions = {s['id']: s for s in skills}
    for term in vocabulary['terms']:
        if term['id'] in definitions:
            term.update(origin=SOURCE, retrieval_hints=definitions[term['id']]['examples'])
    for node in payload['core_nodes']:
        key = node['stable_key']
        if key in retired:
            node['status'] = 'retired'
        if key in definitions:
            s = definitions[key]
            node.update(definition=s['include'], include_scope=s['include'], exclude_scope=s['exclude'],
                        observable_evidence=s['include'], rationale='按明确数学操作划分；结果型证据不推断未观察到的步骤。', evidence_source_ids=[SOURCE])
    for item in payload['fine_term_dispositions']:
        key = item['fine_term_id']
        if key in retired:
            item.update(disposition='maps_to_core', rationale='旧技能含义混杂，兼容时只说明原小节证据。')
        if key in definitions:
            s = definitions[key]
            item.update(definition=s['include'], include_scope=s['include'], exclude_scope=s['exclude'], evidence_source_ids=[SOURCE])
    for mapping in payload['mappings']:
        if mapping['fine_term_id'] in retired:
            mapping.update(stable_key=parents[mapping['fine_term_id']], rationale='旧混合技能上溯原小节，不复制到新技能。')
    payload['relations'] = [r for r in payload['relations'] if r['source_key'] not in retired and r['target_key'] not in retired]
    for r in payload['relations']:
        r['relation_key'] = stable_record_hash('release-relation', RELEASE, r['source_key'], r['target_key'], r['relation_type'])
        if r['source_key'] in definitions:
            r.update(evidence_source_ids=[SOURCE], source_locator=REFERENCE, rationale='教材小节为技能目录位置，知识关联另按题目证据读取。')
    payload['replacements'] = payload.get('replacements', []) + [
        {'retired_key': key, 'replacement_key': parents[key], 'replacement_kind': 'broader', 'rationale': '不把旧混合证据冒充新的细技能证据。'} for key in sorted(retired)]
    payload['sources'] = list({s['source_id']: s for s in payload['sources']}.values()) + [
        {'source_id': SOURCE, 'kind': 'teaching_skill_definition', 'title': '八上可观察数学操作标准', 'reference': REFERENCE}]
    payload['content_hash'] = compute_content_hash(payload)
    result = validate_release(KnowledgeGraphRelease.from_mapping(payload), taxonomy_catalog=vocabulary)
    if not result.valid:
        raise ValueError([(e.path, e.message) for e in result.errors])
    return payload, vocabulary, retired


def select_skills(point, part, evidence, section):
    """Use explicit operation evidence only; ambiguous results stay unresolved."""
    text = ' '.join(str(point.get(k) or '') for k in ('target', 'observable_evidence', 'justification'))
    if str(point.get('target', '')).startswith('作答为'):
        return []  # Whole-question inference cannot establish a demonstrated step.
    chapter = '_'.join(section.split('_')[:6])
    matches = [s['id'] for s in standard()['skills'] if re.search(s['repair_pattern'], text)]
    # These are genuinely different contexts for the same area operation.
    if 'sk_bnu24_math_g8_upper_4_4_205' in matches and not chapter.endswith('_4'):
        matches.remove('sk_bnu24_math_g8_upper_4_4_205')
    if 'sk_bnu24_math_g8_upper_3_3_203' in matches and chapter.endswith('_4'):
        matches.remove('sk_bnu24_math_g8_upper_3_3_203')
    if 'sk_bnu24_math_g8_upper_6_1_206' in matches:
        matches = [k for k in matches if k != 'sk_bnu24_math_g8_upper_6_1_201']
    if 'sk_bnu24_math_g8_upper_5_2_203' in matches:
        matches = [k for k in matches if k != 'sk_bnu24_math_g8_upper_5_2_205']
    # A narrow step may match a broader area rule; keep the specific operation.
    if 'sk_bnu24_math_g8_upper_6_2_201' in matches:
        matches = [k for k in matches if k != 'sk_bnu24_math_g8_upper_6_1_201']
    return matches if len(matches) == 1 else []


def make_plan(db_path, payload, vocabulary, retired, manual=None):
    from question_bank.current_knowledge import CurrentKnowledgeResolver
    from question_bank.solution_evidence.knowledge_links import load_point_links
    resolver = CurrentKnowledgeResolver(KnowledgeGraphRelease.from_mapping(payload), vocabulary)
    conn = sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute('''SELECT s.*,v.evidence_json,q.updated_at AS question_updated_at FROM question_scope_summary s
            JOIN question_solution_evidence_versions v ON v.evidence_version_id=s.evidence_version_id
            JOIN questions q ON q.id=s.question_id WHERE q.is_deleted=0''').fetchall()
        grouped = load_point_links(db_path, [r['evidence_version_id'] for r in rows], payload['predecessor_release_id'], connection=conn)
        plans, counts = [], Counter()
        for row in rows:
            ev = json.loads(row['evidence_json'])
            points = []
            old = grouped.get(row['evidence_version_id'], {})
            changed = False
            for part in ev['parts']:
                for pt in part['evidence_points']:
                    links = old.get(pt['evidence_point_id'], ())
                    direct = {l.stable_key for l in links if l.role == 'direct' and l.resolution_status == 'resolved'}
                    topics = [l.get('fine_term_id', '') for l in pt.get('fine_term_links', [])
                              if l.get('role') == 'direct' and l.get('fine_term_id', '').startswith('kp_bnu24_math_g8_upper_')]
                    sections = {key.rsplit('_', 1)[0] if key.count('_') == 7 else key for key in topics}
                    section = next(iter(sections)) if len(sections) == 1 else row['primary_section_id'] or ''
                    repairable = bool(direct & retired) or (section.startswith('kp_bnu24_math_g8_upper_') and not any(k.startswith('sk_') for k in direct))
                    decision = (manual or {}).get(str(row['question_id']))
                    if decision and decision['evidence_version_id'] != row['evidence_version_id']:
                        raise ValueError('人工核读记录与当前判定点版本不同，请重新核读。')
                    selected = (decision['points'][pt['evidence_point_id']] if decision and pt['evidence_point_id'] in decision['points']
                                else select_skills(pt, part, ev, section) if repairable else [])
                    if selected:
                        if any(not resolver.node(key) or not key.startswith('sk_') for key in selected):
                            raise ValueError('核读记录包含非活动技能')
                        repairable = True
                    new = []
                    if selected:
                        new = [{'term_id': key, 'stable_key': key, 'role': 'direct', 'weight': 1 / len(selected)} for key in selected]
                        counts['skill:' + selected[0]] += 1
                    elif repairable:
                        # Use original fine-topic section rather than preserve a
                        # known wrong cluster parent. Never infer a new skill.
                        resolved_sections = [i.stable_key for i in resolver.resolve(section)]
                        if resolved_sections:
                            new = [{'term_id': resolved_sections[0], 'stable_key': resolved_sections[0], 'role': 'direct', 'weight': 1}]
                    weights = {}
                    for l in links:
                        if l.resolution_status != 'resolved' or (new and l.role == 'direct'):
                            continue
                        for i in resolver.resolve(l.stable_key):
                            pair = (l.role, i.stable_key)
                            weights[pair] = weights.get(pair, 0) + l.weight
                    total = sum(v for (role, key), v in weights.items() if role == 'direct')
                    new.extend({'term_id': key, 'stable_key': key, 'role': role, 'weight': weight / total if role == 'direct' and total else weight}
                               for (role, key), weight in weights.items() if weight > 0)
                    if not any(l['role'] == 'direct' for l in new):
                        raise ValueError(f'Missing direct evidence for question {row["question_id"]}')
                    before = sorted((l.role, l.stable_key, round(l.weight, 8)) for l in links if l.resolution_status == 'resolved')
                    after = sorted((l['role'], l['stable_key'], round(l['weight'], 8)) for l in new)
                    if before != after:
                        changed = True
                        counts['points_changed'] += 1
                        if selected:
                            counts['new_specific_points'] += 1
                    if not any(l['role'] == 'direct' and l['stable_key'].startswith('sk_') for l in new):
                        counts['points_without_skill'] += 1
                        if repairable:
                            counts['section_only_review'] += 1
                    points.append({'part_id': part['part_id'], 'evidence_point_id': pt['evidence_point_id'], 'links': new})
            counts['questions_repaired' if changed else 'questions_carried'] += 1
            plans.append({'question_id': row['question_id'], 'evidence_version_id': row['evidence_version_id'],
                          'question_updated_at': row['question_updated_at'], 'points': points})
        return plans, dict(counts)
    finally:
        conn.close()


def apply_plan(db_path, payload, vocabulary, plans):
    from question_bank.database.schema import connect
    from question_bank.knowledge_graph_release.repository import active_release_id, stage_release, activate_release, rollback_release
    from question_bank.solution_evidence.knowledge_links import replace_point_links, refresh_question_scope_summary
    from question_bank.services.question_write_service import refresh_derived_ownership_tags
    previous = active_release_id(db_path)
    if previous != payload['predecessor_release_id']:
        raise ValueError('活动版本已变，请重新预演。')
    release = KnowledgeGraphRelease.from_mapping(payload)
    stage_release(db_path, release, actor_ref=SOURCE, source_reference=REFERENCE, taxonomy_catalog=vocabulary)
    activate_release(db_path, release.release_id, expected_active_release_id=previous, actor_ref=SOURCE, reason='采用八上可观察操作标准')
    try:
        with connect(db_path) as conn:
            conn.execute('BEGIN IMMEDIATE')
            actual = {int(row['question_id']): (row['evidence_version_id'], row['updated_at']) for row in conn.execute('''
                SELECT s.question_id,s.evidence_version_id,q.updated_at FROM question_scope_summary s
                JOIN questions q ON q.id=s.question_id WHERE q.is_deleted=0''')}
            expected = {int(plan['question_id']): (plan['evidence_version_id'], plan['question_updated_at']) for plan in plans}
            if actual != expected:
                raise ValueError('预演后题目或判定点已变化，已中止写入，请重新预演。')
            for plan in plans:
                replace_point_links(conn, evidence_version_id=plan['evidence_version_id'], question_id=plan['question_id'],
                                    graph_release_id=release.release_id, points=plan['points'], source_reference=SOURCE)
            for plan in plans:
                refresh_question_scope_summary(conn, plan['question_id'])
                refresh_derived_ownership_tags(conn, plan['question_id'])
            if conn.execute('PRAGMA foreign_key_check').fetchone() is not None:
                raise ValueError('引用校验未通过')
    except Exception:
        rollback_release(db_path, previous, expected_active_release_id=release.release_id, actor_ref=SOURCE, reason='修复事务失败，恢复原活动版本')
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preview-db', type=Path)
    parser.add_argument('--write-catalogs', action='store_true')
    parser.add_argument('--manual-decisions', type=Path)
    args = parser.parse_args()
    payload, vocabulary, retired = build_v6()
    if args.write_catalogs:
        for filename, value in [('knowledge_graph_release_v6.json', payload), ('tag_vocabulary_v7.json', vocabulary), ('teaching_skills_g8_upper_v6.json', standard())]:
            (CATALOG / filename).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    summary = {'retired_skills': len(retired), 'new_skills': len(SPECS), 'model_calls': 0}
    if args.preview_db:
        folder = ROOT / 'user_data/previews' / ('g8_skill_v6_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
        folder.mkdir(parents=True, exist_ok=False)
        copy = folder / 'question_bank.db'
        with sqlite3.connect(args.preview_db.resolve().as_uri() + '?mode=ro', uri=True) as src, sqlite3.connect(copy) as dst:
            src.backup(dst)
        manual = json.loads(args.manual_decisions.read_text(encoding='utf-8')) if args.manual_decisions else {}
        plans, counts = make_plan(copy, payload, vocabulary, retired, manual)
        (folder / 'plan.json').write_text(json.dumps(plans, ensure_ascii=False, indent=2), encoding='utf-8')
        apply_plan(copy, payload, vocabulary, plans)
        summary.update(counts, preview_path=str(copy), original_database_changed=False)
        (folder / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
