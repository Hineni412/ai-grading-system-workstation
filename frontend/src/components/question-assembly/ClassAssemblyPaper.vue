<script setup lang="ts">
import AppButton from '../design-system/AppButton.vue'
import StatePanel from '../design-system/StatePanel.vue'
import {computed,ref,watch} from 'vue'
import {useRouter} from 'vue-router'
import {useAssemblyStore} from '../../stores/assembly'
import {createTrainingFromAssembly,defaultPaperRules,type PaperRules,type AssemblyContext} from '../../api/assembly'
const props=defineProps<{title:string;context:AssemblyContext;covered:number;total:number;canAct:boolean}>()
const emit=defineEmits<{edit:[];replace:[id:number]}>()
const assembly=useAssemblyStore(), router=useRouter(), message=ref(''), generating=ref(false)
const rules=computed<PaperRules>(()=>typeof assembly.draft.practice_rules==='object'&&assembly.draft.practice_rules?assembly.draft.practice_rules:defaultPaperRules())
const settings=ref(defaultPaperRules())
watch(rules,r=>{settings.value={...r}},{immediate:true})
const changed=computed(()=>JSON.stringify(settings.value)!==JSON.stringify(defaultPaperRules()))
const violations=computed(()=>assembly.draft.rule_violations??[])
const primaryReason=computed(()=>!valid(settings.value)?'请先核对出卷设置。':JSON.stringify(settings.value)!==JSON.stringify(rules.value)?'出卷设置尚未保存，请重试。':violations.value[0]?.message??(rules.value.purpose==='training'&&(assembly.selectedQuestionCount<8||assembly.selectedQuestionCount>12)?'训练卷需要 8–12 道题，请继续调整。':''))
const minutes=computed(()=>assembly.orderedQuestions.reduce((sum,q)=>sum+(/选择/.test(q.question_type??'')?2:q.question_type==='填空题'?3:8),0))
const types=computed(()=>['选择题','填空题','解答题'].map(type=>({type,count:assembly.orderedQuestions.filter(q=>type==='选择题'?/选择/.test(q.question_type??''):q.question_type===type).length})))
const difficulties=computed(()=>Array.from({length:10},(_,i)=>assembly.orderedQuestions.filter(q=>Math.round(Number(q.difficulty))===i+1).length))
function valid(s:PaperRules){return [s.question_count,s.max_questions_per_skill,s.max_written_questions,s.recent_activity_count,s.difficulty_max].every(Number.isInteger)&&s.question_count>=1&&s.max_questions_per_skill>=1&&s.max_questions_per_skill<=s.question_count&&s.max_written_questions>=0&&s.max_written_questions<=s.question_count&&s.recent_activity_count>=0&&s.difficulty_max>=1&&s.difficulty_max<=10&&(s.purpose!=='training'||(s.question_count>=8&&s.question_count<=12))}
async function saveSettings(){
 if(!valid(settings.value)){message.value='请核对设置范围；训练卷题数为 8–12。';return}
 const ok=await assembly.save({...assembly.draft,practice_rules:{...settings.value},assembly_context:props.context,title:props.title})
 message.value=ok?'出卷设置已保存。':assembly.message
}
async function restore(){settings.value=defaultPaperRules();await saveSettings()}
async function titleChanged(e:Event){await assembly.save({...assembly.draft,title:(e.target as HTMLInputElement).value,assembly_context:{...props.context,title_generated:false}})}
async function primary(){
 const ok=await assembly.save({...assembly.draft,practice_rules:{...rules.value},assembly_context:props.context,title:props.title})
 if(!ok){message.value=assembly.message;return}
 if(rules.value.purpose==='handout'){emit('edit');return}
 generating.value=true
 try{
  const draft=await createTrainingFromAssembly({request_token:crypto.randomUUID().replace(/-/g,''),class_ids:props.context.class_ids,draft_revision:assembly.draft.revision,rules:rules.value})
  assembly.rememberTrainingDraft(draft.draft_id)
  await router.push({path:'/training',query:{mode:'paper',draft:draft.draft_id}})
 }catch{message.value='训练卷未生成，请核对试卷与出卷设置后重试。'}finally{generating.value=false}
}
</script>
<template>
<aside class="ca-panel ca-paper" aria-label="这张卷"><header class="ca-head"><h2>这张卷</h2><span class="ca-badge">{{rules.purpose==='handout'?'讲义':'训练卷'}}</span></header><div class="ca-body">
 <input class="app-input ca-title" aria-label="试卷标题" :value="title" :disabled="assembly.saveState==='saving'" @change="titleChanged"><p class="ca-paper-summary"><b>{{assembly.selectedQuestionCount}}</b> 题 · 约 {{minutes}} 分钟 · {{rules.purpose==='handout'?'只打印':'回收批改'}}</p>
 <h3>题型构成</h3><div v-for="item in types" :key="item.type" class="ca-type"><span>{{item.type.slice(0,-1)}}</span><div class="ca-rate"><i :style="{width:item.count/Math.max(1,assembly.selectedQuestionCount)*100+'%'}" /></div><small>{{item.type==='解答题'?item.count+'/'+rules.max_written_questions+' '+(item.count>rules.max_written_questions?'超出':item.count===rules.max_written_questions?'已满':''):item.count}}</small></div>
 <h3>难度分布</h3><div class="ca-histogram"><div v-for="(n,i) in difficulties" :key="i"><i :style="{height:Math.max(2,n/Math.max(1,...difficulties)*32)+'px'}" /><small>{{i+1}}</small></div></div><p class="ca-coverage">覆盖失分题 <b>{{covered}} / {{total}}</b></p>
 <ol class="ca-paper-list"><li v-for="(q,i) in assembly.orderedQuestions" :key="q.id" :class="{exceeded:violations.some(v=>v.question_id===q.id)}"><span>{{i+1}}</span><div><strong class="ca-paper-stem">{{q.question_text.replace(/\[\[IMAGE:.*?\]\]/g,'').replace(/<u>\s*<\/u>/gi,'____').replace(/<\/?u>/gi,'')}}</strong><small>{{q.question_type}} · 难度 {{q.difficulty??'待定'}}</small><small>{{context.sources[q.id]?(context.sources[q.id]?.original?'原题 ':'补 ')+context.sources[q.id]?.label:'手动选题'}}</small><p v-for="v in violations.filter(v=>v.question_id===q.id)" :key="v.code">超出：{{v.message}}</p></div><div class="ca-paper-actions"><AppButton :disabled="!canAct" variant="ghost" size="small" @click="emit('replace',q.id)">换一题</AppButton><AppButton :disabled="assembly.saveState==='saving'" variant="ghost" size="small" @click="assembly.removeQuestion(q.id)">移出</AppButton></div></li></ol><StatePanel v-if="!assembly.selectedQuestionCount" kind="empty" compact title="从候选题加入，或用快速起草形成初稿。" />
 <details class="ca-settings"><summary>出卷设置 <span v-if="changed" class="ca-badge">已改动</span><small>{{rules.question_count}} 题 · 同技能 ≤ {{rules.max_questions_per_skill}} · 解答 ≤ {{rules.max_written_questions}}</small></summary><div class="ca-settings-grid" @change="saveSettings"><label>用途<select v-model="settings.purpose" class="app-input" aria-label="出卷用途"><option value="handout">讲义 · 只打印</option><option value="training">训练卷 · 回收批改</option></select></label><label>每卷题数<input v-model.number="settings.question_count" class="app-input" aria-label="每卷题数" type="number" :min="settings.purpose==='training'?8:1" :max="settings.purpose==='training'?12:undefined"></label><label>难度上限<input v-model.number="settings.difficulty_max" class="app-input" aria-label="难度上限" type="number" min="1" max="10"></label><label>同一技能最多<input v-model.number="settings.max_questions_per_skill" class="app-input" aria-label="同一技能最多" type="number" min="1" :max="settings.question_count"></label><label>解答题最多<input v-model.number="settings.max_written_questions" class="app-input" aria-label="解答题最多" type="number" min="0" :max="settings.question_count"></label><label>近期原题排除次数<input v-model.number="settings.recent_activity_count" class="app-input" aria-label="近期原题排除次数" type="number" min="0"><small>0 = 不排除</small></label></div><AppButton variant="ghost" size="small" @click="restore">恢复默认</AppButton><p class="ca-muted">改动只对这张卷生效</p></details>
 <div class="ca-checks"><p>✓ 题数 {{assembly.selectedQuestionCount}} / 目标 {{rules.question_count}}</p><p>{{violations.some(v=>v.code==='skill')?'!':'✓'}} 同技能最多 {{rules.max_questions_per_skill}} 道</p><p>{{violations.some(v=>v.code==='written')?'!':'✓'}} 解答题最多 {{rules.max_written_questions}} 道</p><p>{{violations.some(v=>v.code==='difficulty')?'!':'✓'}} 难度不超过 {{rules.difficulty_max}} 级</p><p>{{violations.some(v=>['recent','similar'].includes(v.code))?'!':'✓'}} 近期原题与相似题检查</p></div>
 <AppButton class="ca-primary" :disabled="!canAct||generating||!assembly.selectedQuestionCount||Boolean(primaryReason)" variant="primary" @click="primary">{{generating?'正在生成…':rules.purpose==='handout'?'去整理与导出 →':'生成训练卷 →'}}</AppButton><p v-if="primaryReason" class="ca-blocked">{{primaryReason}}</p><p class="ca-muted ca-purpose-note">{{rules.purpose==='handout'?'讲义不回收、不更新掌握度。':'全班同一套卷：审核题目 → 打印试卷 → 回收批改。结果更新这些学生的掌握度，考试分数不受影响。回收批改会调用模型、产生费用，批改前在训练页确认。'}}</p><p v-if="message||assembly.message" class="ca-blocked" role="status">{{message||assembly.message}}</p>
</div></aside>
</template>
