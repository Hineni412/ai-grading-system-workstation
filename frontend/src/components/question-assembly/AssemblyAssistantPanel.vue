<script setup lang="ts">
import {computed,nextTick,onMounted,onUnmounted,ref,watch} from 'vue'
import {fetchStudents} from '../../api/students'
import {defaultPaperRules,fetchAssemblyQuickDraft,type AssemblyExamQuestion,type AssemblyQuestion,type AssemblyContext} from '../../api/assembly'
import {knowledgeLeafLabel} from '../../api/question-bank'
import {useAssemblyStore} from '../../stores/assembly'
import {useAssemblyAssistantStore} from '../../stores/assembly-assistant'
import {useCurriculumScopeStore} from '../../stores/curriculum-scope'
import QuestionContentRenderer from '../question-bank/QuestionContentRenderer.vue'
import ClassAssemblyPaper from './ClassAssemblyPaper.vue'
import '../../styles/class-assembly.css'
const props=defineProps<{initialSkill?:string}>(), emit=defineEmits<{edit:[]}>()
const assembly=useAssemblyStore(), assistant=useAssemblyAssistantStore(), curriculum=useCurriculumScopeStore()
const classes=ref<string[]>([]), rosterReady=ref(false), scopeMessage=ref(''), actionMessage=ref('')
const showAllSkills=ref(false), originalExpanded=ref(false), expanded=ref(new Set<number>()), similar=ref(new Map<number,AssemblyQuestion[]>())
const relative=ref(''), quickBusy=ref(false), blocked=ref<Record<number,string>>({}), replacingId=ref<number|null>(null)
const original=ref<AssemblyQuestion|null>(null), leftList=ref<HTMLElement|null>(null)
const relativeQuestions=ref<AssemblyQuestion[]>([]), relativeLimit=ref(12), relativeLoading=ref(false)
const rules=computed(()=>typeof assembly.draft.practice_rules==='object' && assembly.draft.practice_rules?assembly.draft.practice_rules:defaultPaperRules())
const selectedClasses=computed(()=>assistant.filters.class_ids??[]), exams=computed(()=>assistant.examResult?.exams??[])
const selectedExams=computed(()=>exams.value.filter(e=>assistant.filters.session_ids?.includes(e.session_id)))
const lossQuestions=computed(()=>{
 const items=selectedExams.value.flatMap(e=>e.questions).filter(q=>q.class_rate!==null&&(assistant.threshold===100||q.class_rate!*100<assistant.threshold))
 return assistant.sort==='loss'?items.sort((a,b)=>a.class_rate!-b.class_rate!||a.key.localeCompare(b.key,'zh-CN',{numeric:true})):items.sort((a,b)=>a.session_id-b.session_id||a.question_id.localeCompare(b.question_id,'zh-CN',{numeric:true}))
})
const selected=computed(()=>lossQuestions.value.find(q=>q.key===assistant.selectedExamKey))
const skillGroups=computed(()=>{
 const points=(assistant.result?.weaknesses??[]).filter(p=>p.knowledge_key.startsWith('sk_')&&(showAllSkills.value||p.weak_student_count>0))
 const groups=new Map<string,typeof points>()
 for(const p of points){const group=p.knowledge_point.split('｜').slice(0,-1).join(' · ')||'当前学期';groups.set(group,[...(groups.get(group)??[]),p])}
 return [...groups].map(([label,points])=>({label,points}))
})
function classLabel(name:string){return /班$/.test(name)?name:name+'班'}
function questionLabel(id:string){return id.replace(/^Q/,'').replace(/\(P(\d+)\)/g,'($1)')}
function examShortTitle(id:number){return examTitle(id).replace(/学情反馈$/,'')}
const title=computed(()=>assembly.draft.title&&!assembly.draft.assembly_context?.title_generated?assembly.draft.title:(curriculum.selectedVolume?.grade??'')+selectedClasses.value.map(n=>n.replace(/班$/,'')).join('、')+'班 '+(selectedClasses.value.length===1?(selectedExams.value[0]?.title.match(/第[一二三四五六七八九十\d]+周/)?.[0]??''):'')+'补偿练习')
const questionMap=computed(()=>new Map([...assistant.questions,...relativeQuestions.value].map(q=>[q.id,q])))
const originalDifficulty=computed(()=>original.value?.difficulty?Number(original.value.difficulty):selected.value?.difficulty)
function difficultyRelation(d:number|null|undefined){const b=originalDifficulty.value;return !b||d==null?'':d<b-.8?'更基础':d>b+.8?'更难':'相当'}
const candidatePool=computed(()=>(assistant.result?.candidates??[]).filter(c=>!relative.value||difficultyRelation(c.difficulty)===relative.value))
const candidates=computed(()=>candidatePool.value.slice(0,relative.value?relativeLimit.value:assistant.visibleCount).flatMap(c=>{const question=questionMap.value.get(c.question_id);return question?[{...c,question}]:[]}))
let relativeSerial=0
async function loadRelative(){const n=++relativeSerial;relativeLoading.value=true;try{const items=await assistant.previewsFor(candidatePool.value.slice(0,relativeLimit.value).map(c=>c.question_id));if(n===relativeSerial)relativeQuestions.value=items}catch{if(n===relativeSerial)actionMessage.value='筛选题目暂时无法读取，请重试。'}finally{if(n===relativeSerial)relativeLoading.value=false}}
watch([relative,()=>assistant.result,originalDifficulty],()=>{relativeQuestions.value=[];relativeLimit.value=12;if(relative.value)void loadRelative();else{relativeSerial++;relativeLoading.value=false}})
async function moreCandidates(){if(relative.value){relativeLimit.value+=12;await loadRelative()}else await assistant.loadMore()}
const hasMore=computed(()=>relative.value?relativeLimit.value<candidatePool.value.length:assistant.hasMore)
const busy=computed(()=>assistant.state==='loading'||assistant.waiting||assistant.isStale||assistant.examState==='loading'||relativeLoading.value)
const canAct=computed(()=>!busy.value&&assistant.state!=='error'&&assistant.examState!=='error'&&rosterReady.value&&assembly.loadState!=='loading'&&assembly.loadState!=='error'&&assembly.saveState!=='saving'&&!quickBusy.value)
const sources=computed(()=>assembly.draft.assembly_context?.sources??{})
function questionSkills(q:AssemblyQuestion){return q.skill_keys??sources.value[q.id]?.skill_keys??[]}
const coveredSkills=computed(()=>new Set(assembly.orderedQuestions.flatMap(questionSkills)))
function coverage(q:AssemblyExamQuestion){
 const n=assembly.draft.order_ids.filter(id=>sources.value[id]?.key===q.key).length
 if(n)return '已配 '+n+' 题'
 const shared=q.skill_keys.find(k=>coveredSkills.value.has(k))
 if(!shared)return ''
 const other=lossQuestions.value.find(v=>v.key!==q.key&&v.skill_keys.includes(shared)&&assembly.draft.order_ids.some(id=>sources.value[id]?.key===v.key))
 return other?'与 '+other.question_id+' 同技能，一起覆盖':'同技能已配题，一起覆盖'
}
const coveredCount=computed(()=>lossQuestions.value.filter(q=>Boolean(coverage(q))).length)
function percent(v:number|null){return v===null?'暂无成绩':Math.round(v*100)+'%'}
function examTitle(id:number){return exams.value.find(e=>e.session_id===id)?.title??'考试'}
function leaf(s:string){return knowledgeLeafLabel(s).replace(/^技能[·：:]\s*/,'')}
function causeTop(q:AssemblyExamQuestion){return [...(q.cause_category_counts??[])].filter(c=>c.count).sort((a,b)=>b.count-a.count).slice(0,2)}
const COLORS=['#6c9b8a','#75aebb','#ba9666','#937eae','#a09173','#83a2a8','#b57874']
const causeHint=computed(()=>{const c=selected.value&&causeTop(selected.value)[0]?.category;return !c?'':c==='未作答'?'可能偏难或时间不够，建议配更基础的题':c==='计算与化简'?'适合配同类型计算变式':'以「'+c+'」为主，建议配同技能变式'})
function context():AssemblyContext{return {class_ids:[...selectedClasses.value],session_ids:[...(assistant.filters.session_ids??[])],curriculum_volume_id:assistant.filters.curriculum_volume_id,title_generated:!assembly.draft.title||assembly.draft.assembly_context?.title_generated,sources:{...sources.value}}}
async function add(id:number,source=selected.value,isOriginal=false){
 if(!canAct.value||assembly.draft.basket_ids.includes(id))return
 const next=context()
 if(source)next.sources[id]={key:source.key,label:examTitle(source.session_id)+'·'+source.question_id,skill_keys:source.skill_keys,original:isOriginal}
 if(replacingId.value)delete next.sources[replacingId.value]
 const ids=assembly.draft.order_ids.filter(q=>q!==replacingId.value)
 const ok=await assembly.save({...assembly.draft,practice_rules:{...rules.value},assembly_context:next,title:title.value,basket_ids:[...ids,id],order_ids:replacingId.value?assembly.draft.order_ids.map(q=>q===replacingId.value?id:q):[...ids,id],sections:assembly.draft.sections.map(s=>({...s,question_ids:s.question_ids.map(q=>q===replacingId.value?id:q)}))})
 if(ok){blocked.value={};replacingId.value=null;actionMessage.value='已加入试卷篮，可继续配题。'}else blocked.value[id]=assembly.message
}
async function replaceSkill(id:number){const c=candidates.value.find(c=>c.question_id===id);const q=assembly.orderedQuestions.find(q=>questionSkills(q).some(k=>c?.target_keys.includes(k)));if(q){replacingId.value=q.id;await add(id)}}
async function selectQuestion(q:AssemblyExamQuestion){relative.value='';originalExpanded.value=false;replacingId.value=null;await assistant.selectExam(q.key,q.skill_keys)}
async function changeClasses(ids:string[]){if(!ids.length)return;assistant.changeScope({class_ids:ids});scopeMessage.value='试卷保留；覆盖情况按新依据重算';await assistant.loadExams()}
function toggleClass(n:string){void changeClasses(selectedClasses.value.includes(n)?selectedClasses.value.filter(v=>v!==n):[...selectedClasses.value,n])}
async function changeExams(ids:number[]){if(!ids.length)return;assistant.changeScope({session_ids:ids});scopeMessage.value='试卷保留；覆盖情况按新依据重算';await chooseFirst()}
async function chooseFirst(){if(assistant.view==='exam'&&lossQuestions.value[0])await selectQuestion(lossQuestions.value[0]);else if(assistant.canSearch)await assistant.search()}
watch(()=>assistant.examState,s=>{if(s==='ready'&&!assistant.result)void chooseFirst()})
watch(()=>[assistant.threshold,assistant.sort],()=>{if(!selected.value&&lossQuestions.value[0])void selectQuestion(lossQuestions.value[0])})
watch(()=>assistant.view,()=>{relative.value='';if(assistant.view==='exam')void chooseFirst();else{const points=skillGroups.value.flatMap(g=>g.points);const target=points.find(p=>p.knowledge_key===assistant.selectedKey)??points[0];if(target)assistant.selectTarget(target.knowledge_key);else if(!assistant.result)void assistant.search()}})
watch(()=>[rules.value.recent_activity_count,rules.value.difficulty_max,rules.value.purpose],()=>{Object.assign(assistant.filters,{recent_activity_count:rules.value.recent_activity_count,difficulty_max:rules.value.difficulty_max,purpose:rules.value.purpose});assistant.scheduleSearch()},{immediate:true})
watch(()=>assistant.filters.question_type,()=>assistant.scheduleSearch())
let originalSerial=0
watch(selected,async q=>{
 const n=++originalSerial;original.value=null
 if(q?.bank_question_id)try{const items=await assistant.previewsFor([q.bank_question_id]);if(n===originalSerial)original.value=items[0]??null}
 catch{if(n===originalSerial)original.value=null}
},{immediate:true})
let appliedSkill=''
watch(()=>assistant.result,r=>{if(props.initialSkill&&r&&appliedSkill!==props.initialSkill){appliedSkill=props.initialSkill;if(r.weaknesses.some(p=>p.knowledge_key===props.initialSkill)){assistant.view='skill';assistant.selectTarget(props.initialSkill)}else actionMessage.value='技能不在当前班级与章节结果中，当前选择已保留。'}})
watch(()=>curriculum.selectedVolumeId,id=>{if(assistant.filters.curriculum_volume_id!==(id??'')){assistant.changeScope({curriculum_volume_id:id??''});if(rosterReady.value)void assistant.loadExams()}},{immediate:true})
async function quickDraft(){
 quickBusy.value=true;actionMessage.value='正在为还没配题的失分题起草…';const revision=assembly.draft.revision, basis=JSON.stringify([assistant.filters,rules.value,assistant.threshold,assistant.sort])
 try{
  const r=await fetchAssemblyQuickDraft({...assistant.filters,question_ids:[...assembly.draft.order_ids],rules:{...rules.value},threshold:assistant.threshold,sort:assistant.sort})
  if(assembly.draft.revision!==revision||basis!==JSON.stringify([assistant.filters,rules.value,assistant.threshold,assistant.sort])){actionMessage.value='试卷或依据已变化，请重新起草；原草稿保留。';return}
  const next=context()
  for(const a of r.additions)next.sources[a.question_id]={key:a.source.key,label:examTitle(a.source.session_id)+'·'+a.source.question_id,skill_keys:a.source.skill_keys}
  const ids=[...assembly.draft.order_ids,...r.question_ids]
  const ok=!r.question_ids.length||await assembly.save({...assembly.draft,practice_rules:{...rules.value},assembly_context:next,title:title.value,basket_ids:ids,order_ids:ids})
  const labels:Record<string,string>={skill:'同技能已配',written:'解答题已满',difficulty:'难度超限',similar:'相似',unavailable:'无可配题'}
  actionMessage.value=ok?'已起草 '+r.question_ids.length+' 题，试卷共 '+ids.length+' / '+rules.value.question_count+' 题。'+Object.entries(r.skipped).filter(([,n])=>n>0).map(([k,n])=>labels[k]+' '+n).join('；'):assembly.message
 }catch{actionMessage.value='起草暂时失败，原试卷保留，请重试。'}finally{quickBusy.value=false}
}
function switchSource(id:number){const q=lossQuestions.value.find(q=>q.key===sources.value[id]?.key);if(q){assistant.view='exam';void selectQuestion(q).then(()=>{replacingId.value=id})}else actionMessage.value='来源不在当前依据中，请先选择对应考试或技能。'}
async function keys(e:KeyboardEvent){if(e.ctrlKey||e.metaKey||e.altKey||(e.target instanceof Element&&e.target.closest('input,select,textarea,[contenteditable]')))return;const d=e.key.toLowerCase()==='w'?-1:e.key.toLowerCase()==='s'?1:0;if(!d)return;e.preventDefault();if(assistant.view==='exam'){const list=lossQuestions.value;const i=list.findIndex(q=>q.key===assistant.selectedExamKey);const q=list[Math.max(0,Math.min(list.length-1,i+d))];if(q)await selectQuestion(q)}else{const list=skillGroups.value.flatMap(g=>g.points);const i=list.findIndex(p=>p.knowledge_key===assistant.selectedKey);const p=list[Math.max(0,Math.min(list.length-1,i+d))];if(p)assistant.selectTarget(p.knowledge_key)};await nextTick();leftList.value?.querySelector('.is-selected')?.scrollIntoView?.({block:'nearest'})}
onMounted(async()=>{window.addEventListener('keydown',keys);void curriculum.initialize();if(assembly.loadState==='idle')void assembly.load();try{classes.value=[...new Set((await fetchStudents()).flatMap(s=>s.class_name?[s.class_name]:[]))].sort((a,b)=>a.localeCompare(b,'zh-CN',{numeric:true}));const valid=selectedClasses.value.filter(c=>classes.value.includes(c));assistant.changeScope({class_ids:valid.length?valid:classes.value.slice(0,1)});rosterReady.value=true;if(!assistant.examResult)await assistant.loadExams()}catch{actionMessage.value='班级名册暂时无法读取，请重试。'}})
onUnmounted(()=>window.removeEventListener('keydown',keys))
</script>

<template>
<section class="class-assembly" aria-label="班级组卷">
 <div class="ca-scope ca-panel">
  <div class="ca-scope-row"><span class="ca-label">班级</span><button class="ca-chip" :class="{selected:selectedClasses.length===classes.length}" :disabled="!rosterReady" @click="changeClasses([...classes])">全选</button><button v-for="name in classes" :key="name" class="ca-chip" :class="{selected:selectedClasses.includes(name)}" :aria-pressed="selectedClasses.includes(name)" @click="toggleClass(name)">{{classLabel(name)}}</button><span class="ca-muted">合计 {{assistant.examResult?.student_count??assistant.result?.student_count??'—'}} 人</span></div>
  <div class="ca-scope-row"><span class="ca-label">依据考试</span><button class="ca-chip" :disabled="!exams.length" :class="{selected:selectedExams.length===exams.length&&exams.length>0}" @click="changeExams(exams.map(e=>e.session_id))">全选</button><button v-for="exam in exams" :key="exam.session_id" class="ca-chip ca-exam-chip" :class="{selected:assistant.filters.session_ids?.includes(exam.session_id)}" :aria-pressed="assistant.filters.session_ids?.includes(exam.session_id)" @click="changeExams(assistant.filters.session_ids?.includes(exam.session_id)?selectedExams.filter(e=>e.session_id!==exam.session_id).map(e=>e.session_id):[...(assistant.filters.session_ids??[]),exam.session_id])"><b>{{exam.title}}</b><small>{{exam.date.slice(0,10)}} · {{exam.student_count}} 人<template v-if="exam.class_ids.length<selectedClasses.length"> · 仅{{exam.class_ids.map(classLabel).join('、')}}</template></small></button><span v-if="assistant.examState==='loading'">正在读取考试依据…</span><span v-else-if="assistant.examState==='ready'&&!exams.length" class="ca-muted">本学期暂无有效考试成绩</span><button v-if="assistant.examState==='error'" class="qb-link" @click="assistant.loadExams()">重试</button><label class="ca-training-toggle"><input v-model="assistant.includeTraining" type="checkbox">叠加训练掌握情况</label></div>
 </div>
 <p v-if="scopeMessage" class="ca-scope-note" role="status">{{scopeMessage}}</p>
 <div class="ca-columns">
  <section class="ca-panel ca-needs"><header class="ca-head"><h2>要补的点</h2><button class="qb-button" :disabled="!canAct||!lossQuestions.length||assembly.selectedQuestionCount>=rules.question_count" @click="quickDraft">{{quickBusy?'起草中…':'快速起草'}}</button></header><div class="ca-body">
   <div class="ca-segment"><button :class="{selected:assistant.view==='exam'}" @click="assistant.view='exam'">按考试题</button><button :class="{selected:assistant.view==='skill'}" @click="assistant.view='skill'">按技能</button></div>
   <template v-if="assistant.view==='exam'"><div class="ca-controls"><select v-model.number="assistant.threshold" class="app-input" aria-label="得分率阈值"><option :value="70">低于 70%</option><option :value="80">低于 80%</option><option :value="60">低于 60%</option><option :value="100">全部</option></select><select v-model="assistant.sort" class="app-input" aria-label="失分题排序"><option value="loss">失分最多优先</option><option value="exam">按考试和题号</option></select></div><p class="ca-muted">{{lossQuestions.length}} 道题 · W / S 切换</p><div ref="leftList" class="ca-need-list">
    <button v-for="q in lossQuestions" :key="q.key" class="ca-need" :class="{'is-selected':q.key===assistant.selectedExamKey}" @click="selectQuestion(q)"><div class="ca-need-top"><strong>{{examShortTitle(q.session_id)}} · {{questionLabel(q.question_id)}}</strong><span>{{percent(q.class_rate)}}</span></div><small>{{q.question_type}}</small><div class="ca-rate"><i :style="{width:(q.class_rate??0)*100+'%'}" /></div><div class="ca-causes"><span v-for="c in causeTop(q)" :key="c.category">{{c.category}} {{c.count}}</span><span v-if="q.cause_category_counts===null">错因未整理</span></div><small>{{q.skills.map(s=>leaf(s.label)).join(' · ')||'暂无关联技能'}}</small><small v-if="selectedClasses.length>1">{{q.class_rates.map(c=>classLabel(c.class_id)+' '+percent(c.class_rate)).join(' · ')}}<template v-if="q.class_rates.length<selectedClasses.length"> · 仅{{q.class_rates.map(c=>classLabel(c.class_id)).join('、')}}参加</template></small><span v-if="coverage(q)" class="ca-badge">{{coverage(q)}}</span><small v-if="!q.bank_question_id">未关联题库 · 仅按技能配题</small></button>
   </div><p v-if="!lossQuestions.length" class="ca-empty">当前依据下没有符合阈值的考试题，可调整阈值或切换按技能。</p></template>
   <template v-else><p class="ca-muted">{{assistant.includeTraining?'本学期考试与最新训练掌握情况':'本学期考试掌握情况'}}；需关注 = 还不稳 + 明显薄弱</p><div ref="leftList" class="ca-need-list"><template v-for="group in skillGroups" :key="group.label"><h3 class="ca-skill-group">{{group.label}}</h3><button v-for="point in group.points" :key="point.knowledge_key" class="ca-need" :class="{'is-selected':assistant.selectedKey===point.knowledge_key}" @click="assistant.selectTarget(point.knowledge_key)"><strong>{{leaf(point.knowledge_point)}}</strong><small>{{point.weak_student_count}} 人需关注 / {{point.evidence_student_count}} 人有证据</small><span v-if="!point.weak_student_count" class="ca-badge">可搭配巩固</span></button></template></div><button class="qb-link" @click="showAllSkills=!showAllSkills">{{showAllSkills?'只看需关注':'展开全部技能'}}</button></template>
   <p class="ca-muted ca-local">本地筛选 · 无模型费用</p>
  </div></section>
  <section class="ca-panel ca-matching" aria-label="配题"><header class="ca-head"><h2>配题</h2><span class="ca-muted">{{replacingId?'请选择替换题':'依据学情逐题搭配'}}</span></header><div class="ca-body">
   <article v-if="assistant.view==='exam'&&selected" class="ca-original"><header><strong>{{examShortTitle(selected.session_id)}} · {{questionLabel(selected.question_id)}}</strong><span>{{selected.question_type}} · {{selected.full_score}} 分 · {{selectedClasses.length>1?'合计':'本班'}} {{percent(selected.class_rate)}}</span></header><p v-if="selectedClasses.length>1" class="ca-muted">{{selected.class_rates.map(c=>classLabel(c.class_id)+' '+percent(c.class_rate)+'（'+c.student_count+'人）').join(' · ')}}</p><div :class="{'ca-clamp':!originalExpanded}"><QuestionContentRenderer :blocks="originalExpanded?original?.rich_content?.question_blocks:undefined" :fallback="(original?.question_text||selected.question_text).replace(/\[\[IMAGE:.*?\]\]/g,'').replace(/<u>\s*<\/u>/gi,'____').replace(/<\/?u>/gi,'')||'题干含图片，展开查看完整原题。'" media-mode="list" dense typeset-text /></div><button class="qb-link" @click="originalExpanded=!originalExpanded">{{originalExpanded?'收起题干':'展开题干'}}</button><template v-if="selected.cause_category_counts"><div class="ca-stack"><i v-for="(c,i) in selected.cause_category_counts" :key="c.category" :style="{background:COLORS[i],flex:c.count||0.001}" /></div><div class="ca-legend"><span v-for="(c,i) in selected.cause_category_counts" :key="c.category"><i :style="{background:COLORS[i]}" />{{c.category}} {{c.count}}</span></div><p v-if="causeHint" class="ca-hint">{{causeHint}}</p></template><p v-else class="ca-muted">错因未整理</p><div class="ca-tags"><span v-for="skill in selected.skills" :key="skill.key">{{leaf(skill.label)}}</span></div><p v-if="rules.recent_activity_count>0&&selected.bank_question_id" class="ca-muted">本班最近 {{rules.recent_activity_count}} 次已做过的原题，按出卷设置不重复出</p><button v-else-if="selected.bank_question_id" class="qb-button" :disabled="!canAct||assembly.draft.basket_ids.includes(selected.bank_question_id)" @click="add(selected.bank_question_id,selected,true)">{{assembly.draft.basket_ids.includes(selected.bank_question_id)?'原题已加入':'加入原题'}}</button></article>
   <div class="ca-candidate-head"><strong>候选题 <small>{{candidatePool.length}}</small></strong><select v-model="assistant.filters.question_type" class="app-input" aria-label="题型"><option value="">全部题型</option><option>选择题</option><option>多选题</option><option>填空题</option><option>解答题</option></select></div><div v-if="assistant.view==='exam'&&originalDifficulty" class="ca-relative"><span>相对原题</span><button v-for="label in ['','更基础','相当','更难']" :key="label" class="ca-chip" :class="{selected:relative===label}" @click="relative=label">{{label||'全部'}}</button></div>
   <p v-if="busy" role="status" class="ca-muted">正在按新的选择更新候选题…</p><p v-if="assistant.message" class="ca-feedback" role="status">{{assistant.message}}</p><p v-if="actionMessage" class="ca-feedback" role="status">{{actionMessage}}</p><p v-if="!busy&&!candidates.length" class="ca-empty">当前条件下没有直接考查这个目标的合适题目。可调整题型、出卷设置或所选目标。</p>
   <article v-for="(c,index) in candidates" :key="c.question_id" class="ca-candidate assistant-question" :class="{'is-in-basket':assembly.draft.basket_ids.includes(c.question_id)}"><header><strong>候选 {{index+1}} · {{c.question.question_type}}</strong><small>难度 {{c.difficulty??c.question.difficulty??'待定'}}<template v-if="assistant.view==='exam'"> · {{difficultyRelation(c.difficulty)}}</template></small><button class="qb-button" :disabled="!canAct||assembly.draft.basket_ids.includes(c.question_id)" @click="add(c.question_id)">{{assembly.draft.basket_ids.includes(c.question_id)?'已加入':'加入'}}</button></header><QuestionContentRenderer :blocks="c.question.rich_content?.question_blocks" :fallback="c.question.question_text" media-mode="list" paper-media-flow dense typeset-text /><div class="ca-candidate-foot"><span class="ca-badge">{{c.match_level?c.match_level+'级 · ':''}}{{c.match_label}}</span><span>适合 {{c.suitable_student_count??0}} 人 · 补弱 {{c.remediation_student_count??0}} · 巩固 {{c.consolidation_student_count??0}} · 新练习 {{c.new_practice_student_count??0}}</span></div><div v-if="c.similar_question_ids?.length" class="ca-similar"><button class="qb-link" @click="similar.has(c.question_id)?similar.delete(c.question_id):assistant.previewsFor(c.similar_question_ids).then(items=>similar.set(c.question_id,items))">高度相似 {{c.similar_question_ids.length}} 题 · {{similar.has(c.question_id)?'收起':'查看'}}</button><article v-for="q in similar.get(c.question_id)??[]" :key="q.id"><QuestionContentRenderer :blocks="q.rich_content?.question_blocks" :fallback="q.question_text" dense typeset-text /><button class="qb-button" :disabled="!canAct||assembly.draft.basket_ids.includes(q.id)" @click="add(q.id)">加入</button></article></div><button class="qb-link" @click="expanded.has(c.question_id)?expanded.delete(c.question_id):expanded.add(c.question_id)">{{expanded.has(c.question_id)?'收起解析':'查看解析'}}</button><QuestionContentRenderer v-if="expanded.has(c.question_id)" :blocks="c.question.rich_content?.answer_blocks" :fallback="c.question.answer_text||'暂无解析'" dense typeset-text /><div v-if="blocked[c.question_id]" class="ca-blocked" role="status">{{blocked[c.question_id]}}<button v-if="blocked[c.question_id]?.includes('同一技能')" class="qb-link" @click="replaceSkill(c.question_id)">替换</button></div></article>
   <button v-if="hasMore" class="qb-button" :disabled="busy||assistant.loadingMore" @click="moreCandidates()">{{assistant.loadingMore?'读取中…':'显示更多候选题'}}</button>
  </div></section>
  <ClassAssemblyPaper :title="title" :context="context()" :covered="coveredCount" :total="lossQuestions.length" :can-act="canAct" @edit="emit('edit')" @replace="switchSource" />
 </div>
</section>
</template>
