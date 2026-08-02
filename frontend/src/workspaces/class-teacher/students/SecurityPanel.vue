<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { ApiError } from '../../../api/errors'
import { supportApi, type SubjectDeletionPreview } from '../api/support'
import { vaultApi, type VaultBackup, type VaultRestorePreview, type VaultStatus } from '../api/vault'
import { studentR1Api, type DirectorySubject } from '../api/r1'

const props = defineProps<{ token: string; status: VaultStatus | null; subject: DirectorySubject | null }>()
const emit = defineEmits<{ locked: [reason: string]; subjectDeleted: [message: string] }>()
const backups = ref<VaultBackup[]>([])
const backupPassword = ref('')
const currentPin = ref('')
const newPin = ref('')
const restoreFile = ref('')
const restoreSecret = ref('')
const restorePreview = ref<VaultRestorePreview | null>(null)
const restorePhrase = ref('')
const deletionPreview = ref<SubjectDeletionPreview | null>(null)
const deletionPhrase = ref('')
const backupDeletionPhrase = ref('')
const deleteOperationId = ref('')
const message = ref('')
const backupError = ref('')
const restoreState = ref<'select_backup'|'entering_secret'|'verifying'|'impact_review'|'final_confirmation'|'restoring'|'restored_and_locked'|'preview_expired'|'preview_stale'|'restore_failed'|'restore_result_unknown'>('select_backup')
const deleteState = ref<'select_subject'|'loading_impact'|'impact_ready'|'entering_two_phrases'|'deleting'|'deleted'|'preview_stale'|'delete_failed'|'delete_result_unknown'|'deleted_refresh_failed'>('select_subject')
const restoreError = ref('')
const deleteError = ref('')
async function load() {
  backupError.value = ''
  try { backups.value = await vaultApi.listBackups(props.token) }
  catch { backupError.value = '专用备份列表暂时无法读取；保护状态不受影响。' }
}
async function createBackup() {
  if (backupPassword.value.length < 12) return
  const secret = backupPassword.value; backupPassword.value = ''; backupError.value = ''
  try { await vaultApi.createBackup(props.token, secret); message.value='专用加密备份已创建。'; await load() }
  catch { backupError.value = '备份创建失败；现有保险箱和备份保持不变。' }
}
async function changePin() {
  if (!/^\d{6}$/.test(newPin.value)) return
  const oldValue=currentPin.value; const nextValue=newPin.value; currentPin.value=''; newPin.value=''
  try { await vaultApi.changePin(props.token, oldValue, nextValue); emit('locked','PIN 已更改，所有敏感会话已锁定，请用新 PIN 解锁。') }
  catch { message.value='PIN 修改失败；原 PIN 仍然有效。' }
}
async function previewRestore() {
  restoreState.value='verifying'; restoreError.value=''; restorePreview.value=null
  const secret=restoreSecret.value; restoreSecret.value=''
  try { restorePreview.value = await vaultApi.previewRestore(props.token, restoreFile.value, secret, 'password'); restorePhrase.value=''; restoreState.value='impact_review' }
  catch { restoreState.value='restore_failed'; restoreError.value='备份验证失败；当前库保持不变。' }
}
async function confirmRestore() {
  if (!restorePreview.value || restorePhrase.value !== restorePreview.value.confirmation_phrase) return
  restoreState.value='restoring'; restoreError.value=''
  const previewToken=restorePreview.value.preview_token; const phrase=restorePhrase.value
  restorePhrase.value=''
  try { await vaultApi.confirmRestore(props.token, previewToken, phrase); restoreState.value='restored_and_locked'; restorePreview.value=null; emit('locked','完整恢复已完成，敏感区已锁定。') }
  catch (error) {
    if (error instanceof ApiError && error.code.includes('expired')) restoreState.value='preview_expired'
    else if (error instanceof ApiError && error.code.includes('stale')) restoreState.value='preview_stale'
    else {
      restoreState.value='restore_result_unknown'; restorePreview.value=null
      emit('locked','恢复结果暂未确认；敏感区已安全锁定。请重新解锁后核对当前保险箱，不要重复恢复。')
    }
    restoreError.value = restoreState.value === 'preview_expired'
      ? '恢复预览已过期，请重新验证备份；当前库保持不变。'
      : restoreState.value === 'preview_stale'
        ? '预览后当前库已有变化，请重新预览；当前库保持不变。'
        : '恢复结果暂未确认；为避免显示旧数据，敏感区已经锁定。'
  }
}
async function previewDelete() {
  if (!props.subject) return
  deleteState.value='loading_impact'; deleteError.value=''
  try { deletionPreview.value = await supportApi.previewSubjectDeletion(props.token, props.subject.subject_id); deletionPhrase.value=''; backupDeletionPhrase.value=''; deleteOperationId.value=''; deleteState.value='impact_ready' }
  catch { deleteState.value='delete_failed'; deleteError.value='删除影响暂时无法读取；没有删除任何数据。' }
}
async function deleteSubject() {
  if (!props.subject || !deletionPreview.value || deletionPhrase.value !== deletionPreview.value.delete_confirmation_phrase) return
  if (deletionPreview.value.backup_confirmation_phrase && backupDeletionPhrase.value !== deletionPreview.value.backup_confirmation_phrase) return
  deleteState.value='deleting'; deleteError.value=''
  const subjectId=props.subject.subject_id
  if (!deleteOperationId.value) deleteOperationId.value=globalThis.crypto.randomUUID()
  const operationId=deleteOperationId.value
  try {
    await supportApi.deleteSubject(props.token, subjectId, deletionPreview.value, backupDeletionPhrase.value || null, operationId)
  } catch (error) {
    if (error instanceof ApiError && (error.code.includes('preview') || error.status === 409)) {
      deleteState.value='preview_stale'; deleteError.value='删除影响已经变化，请重新预览；没有按旧预览继续删除。'; deletionPreview.value=null; deletionPhrase.value=''; backupDeletionPhrase.value=''; deleteOperationId.value=''; return
    }
    deleteState.value='delete_result_unknown'; deleteError.value='删除结果暂未确认；请查询同一删除操作，不要重新预览或创建新操作。'; return
  }
  deletionPreview.value=null; deletionPhrase.value=''; backupDeletionPhrase.value=''; deleteOperationId.value=''
  try {
    await studentR1Api.directory(props.token, { pageSize: 1 })
    deleteState.value='deleted'; message.value='该学生的支持数据及匿名投影已完整删除。'
  } catch {
    deleteState.value='deleted_refresh_failed'; deleteError.value='删除已完成，列表暂未读回；请稍后刷新，不要重复删除。'
  }
  emit('subjectDeleted', deleteError.value || message.value)
}
onMounted(() => { void load() })
</script>

<template>
  <section class="security">
    <header><div><p>高级数据安全</p><h2>保护、备份、恢复和完整删除</h2></div><span>所有操作只针对班主任专用加密保险箱</span></header>
    <div class="top">
      <section><p class="eyebrow">保护状态</p><dl><div><dt>当前状态</dt><dd>已解锁</dd></div><div><dt>剩余会话</dt><dd>{{ status?.session_expires_in_seconds ?? 0 }} 秒</dd></div><div><dt>保护方式</dt><dd>{{ status?.protection_mode }}</dd></div></dl><h3>更改 6 位 PIN</h3><label><span>当前 PIN</span><input v-model="currentPin" type="password" maxlength="6" inputmode="numeric"></label><label><span>新 PIN</span><input v-model="newPin" type="password" maxlength="6" inputmode="numeric"></label><button type="button" :disabled="!currentPin || !/^\d{6}$/.test(newPin)" @click="changePin">更改并锁定全部会话</button></section>
      <section><p class="eyebrow">完整替换恢复 · {{ restoreState }}</p><ol><li>选择专用备份</li><li>验证备份密码</li><li>核对替换范围</li><li>输入确认短语</li></ol><select v-model="restoreFile" @change="restoreState='entering_secret'"><option value="">选择备份</option><option v-for="backup in backups" :key="backup.backup_id" :value="backup.file_name">{{ backup.created_at.slice(0,10) }} · {{ backup.file_name }}</option></select><input v-model="restoreSecret" type="password" placeholder="备份独立密码"><button type="button" :disabled="!restoreFile || !restoreSecret || restoreState==='verifying' || restoreState==='restoring'" @click="previewRestore">验证并预览</button><div v-if="restorePreview" class="warning"><strong>这会完整替换当前保险箱</strong><p>当前 {{ Object.values(restorePreview.current_scope_counts).reduce((a,b)=>a+b,0) }} 项；备份 {{ Object.values(restorePreview.backup_scope_counts).reduce((a,b)=>a+b,0) }} 项。成功后立即锁定。</p><label><span>输入：{{ restorePreview.confirmation_phrase }}</span><input v-model="restorePhrase" @input="restoreState='final_confirmation'"></label><button type="button" :disabled="restorePhrase!==restorePreview.confirmation_phrase || restoreState==='restoring'" @click="confirmRestore">确认完整替换</button></div><p v-if="restoreError" class="error" role="alert">{{ restoreError }}</p></section>
    </div>
    <section class="backups"><div><p class="eyebrow">专用加密备份</p><h3>普通系统备份不包含这里</h3></div><label><span>备份独立密码（至少 12 字符）</span><input v-model="backupPassword" type="password"></label><button type="button" :disabled="backupPassword.length<12" @click="createBackup">创建专用备份</button><p v-if="backupError" class="error" role="alert">{{ backupError }}</p><table><thead><tr><th>创建时间</th><th>文件</th><th>大小</th><th>状态</th></tr></thead><tbody><tr v-for="backup in backups" :key="backup.backup_id"><td>{{ backup.created_at.slice(0,19) }}</td><td>{{ backup.file_name }}</td><td>{{ Math.ceil(backup.size_bytes/1024) }} KB</td><td>{{ backup.status || '可用' }}</td></tr></tbody></table></section>
    <section class="danger"><div><p class="eyebrow">完整删除学生支持数据 · {{ deleteState }}</p><h3>{{ subject ? `当前选择：${subject.display_name}` : '请先从目录选择学生' }}</h3><p>删除预览会列出学生卡、支持记录、学业证据、AI 复核、关注、行动和匿名投影。</p></div><button type="button" :disabled="!subject || deleteState==='deleting'" @click="previewDelete">查看删除影响</button><div v-if="deletionPreview" class="warning"><ul><li v-for="(count,key) in (deletionPreview.impact_counts ?? {})" :key="key">{{ key }}：{{ count }}</li><li>匿名投影：{{ deletionPreview.projection_count ?? 0 }}</li><li>可能包含该学生的现有专用备份：{{ deletionPreview.affected_backup_count }}</li></ul><label><span>输入：{{ deletionPreview.delete_confirmation_phrase }}</span><input v-model="deletionPhrase" @input="deleteState='entering_two_phrases'"></label><label v-if="deletionPreview.backup_confirmation_phrase"><span>再次输入：{{ deletionPreview.backup_confirmation_phrase }}</span><input v-model="backupDeletionPhrase" @input="deleteState='entering_two_phrases'"></label><button type="button" :disabled="deleteState==='deleting' || deletionPhrase!==deletionPreview.delete_confirmation_phrase || (!!deletionPreview.backup_confirmation_phrase && backupDeletionPhrase!==deletionPreview.backup_confirmation_phrase)" @click="deleteSubject">{{ deleteState==='delete_result_unknown' ? '查询同一删除操作' : '永久删除并清理投影' }}</button></div><p v-if="deleteError" class="error" role="alert">{{ deleteError }}</p></section>
    <p v-if="message" class="success" role="status">{{ message }}</p>
  </section>
</template>

<style scoped>
.security{display:grid;gap:var(--space-4)}header{display:flex;justify-content:space-between;align-items:end;padding:var(--space-5);border:1px solid var(--color-border-default);border-radius:var(--radius-panel);background:var(--color-bg-surface)}header p,.eyebrow{margin:0 0 2px;color:var(--color-accent);font-size:var(--font-size-caption);font-weight:700;letter-spacing:.07em}h2{margin:0;font-size:var(--font-size-h2)}header>span{color:var(--color-text-secondary)}.top{display:grid;grid-template-columns:56fr 44fr;gap:var(--space-4)}.top>section,.backups,.danger{padding:var(--space-5);border:1px solid var(--color-border-default);border-radius:var(--radius-panel);background:var(--color-bg-surface)}dl{display:grid;grid-template-columns:repeat(3,1fr);gap:var(--space-2)}dl div{display:grid;gap:3px}dt{color:var(--color-text-muted)}dd{margin:0;font-weight:700}label{display:grid;gap:var(--space-1);margin-top:var(--space-3);font-weight:650}input,select,button{min-height:40px;padding:0 var(--space-3);border:1px solid var(--color-border-default);border-radius:var(--radius-control);background:var(--color-bg-surface);font:inherit}section>button{margin-top:var(--space-3)}ol{display:grid;grid-template-columns:repeat(4,1fr);gap:var(--space-1);padding:0;list-style-position:inside;font-size:var(--font-size-dense)}.top section>select,.top section>input{width:100%;margin-top:var(--space-2)}.backups{display:grid;grid-template-columns:minmax(0,1fr) minmax(260px,.7fr) auto;align-items:end;gap:var(--space-3)}.backups h3{margin:0}.backups table,.backups .error{grid-column:1/-1}.backups table{width:100%;border-collapse:collapse}th,td{padding:var(--space-2);border-bottom:1px solid var(--color-border-subtle);text-align:left}.danger{display:grid;grid-template-columns:1fr auto;align-items:center;border-color:var(--color-danger)}.danger h3{margin:0}.danger .warning,.danger .error{grid-column:1/-1}.warning{margin-top:var(--space-3);padding:var(--space-4);border-left:3px solid var(--color-warning);background:var(--color-warning-subtle)}.warning button{margin-top:var(--space-3)}.success{color:var(--color-success)}.error{color:var(--color-danger)}@media(max-width:900px){.top{grid-template-columns:1fr}.backups{grid-template-columns:1fr}.danger{grid-template-columns:1fr}header{align-items:flex-start;flex-direction:column}}
</style>
