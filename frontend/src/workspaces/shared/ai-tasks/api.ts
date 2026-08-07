import { apiClient } from '../../../api/client'
import {
  decodeWorkspaceAITask,
  decodeWorkspaceAITaskList,
  type PrepareWorkspaceAITaskInput,
  type WorkspaceAITask,
} from './contracts'

export interface WorkspaceAITaskApi {
  prepare(input: PrepareWorkspaceAITaskInput, signal?: AbortSignal): Promise<WorkspaceAITask>
  dispatch(
    operationId: string,
    preparedTaskId: string,
    requestFingerprint?: string,
    signal?: AbortSignal,
  ): Promise<WorkspaceAITask>
  get(taskId: string, signal?: AbortSignal): Promise<WorkspaceAITask>
  getByOperation(operationId: string, signal?: AbortSignal): Promise<WorkspaceAITask>
  cancel(operationId: string, signal?: AbortSignal): Promise<WorkspaceAITask>
  discard(operationId: string, signal?: AbortSignal): Promise<WorkspaceAITask>
  list(module: WorkspaceAITask['module'], signal?: AbortSignal): Promise<WorkspaceAITask[]>
}

const segment = (value: string) => encodeURIComponent(value)

export const workspaceAITaskApi: WorkspaceAITaskApi = {
  list(module, signal) {
    return apiClient.request(
      `/api/workspace-ai-tasks?module=${encodeURIComponent(module)}`,
      { decode: decodeWorkspaceAITaskList, signal },
    )
  },
  prepare(input, signal) {
    return apiClient.request('/api/workspace-ai-tasks/prepare', {
      method: 'POST',
      body: input,
      decode: decodeWorkspaceAITask,
      signal,
    })
  },
  dispatch(operationId, preparedTaskId, requestFingerprint, signal) {
    return apiClient.request(
      `/api/workspace-ai-tasks/operations/${segment(operationId)}/dispatch`,
      {
        method: 'POST',
        body: {
          prepared_task_id: preparedTaskId,
          ...(requestFingerprint ? { request_fingerprint: requestFingerprint } : {}),
        },
        decode: decodeWorkspaceAITask,
        signal,
      },
    )
  },
  get(taskId, signal) {
    return apiClient.request(`/api/workspace-ai-tasks/${segment(taskId)}`, {
      decode: decodeWorkspaceAITask,
      signal,
    })
  },
  getByOperation(operationId, signal) {
    return apiClient.request(
      `/api/workspace-ai-tasks/operations/${segment(operationId)}/current`,
      { decode: decodeWorkspaceAITask, signal },
    )
  },
  cancel(operationId, signal) {
    return apiClient.request(
      `/api/workspace-ai-tasks/operations/${segment(operationId)}/cancel`,
      { method: 'POST', decode: decodeWorkspaceAITask, signal },
    )
  },
  discard(operationId, signal) {
    return apiClient.request(
      `/api/workspace-ai-tasks/operations/${segment(operationId)}/discard`,
      { method: 'POST', decode: decodeWorkspaceAITask, signal },
    )
  },
}
