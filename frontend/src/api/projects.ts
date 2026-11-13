import { http } from './client';
import type {
  ProjectDetail,
  ProjectFilterOptions,
  ProjectList,
  ProjectStats,
} from '../types/models';

export interface ProjectQuery {
  q?: string;
  region?: string;
  category?: string;
  status?: string;
  conflict?: string;
  page?: number;
  page_size?: number;
}

function toQuery(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== '' && value !== null) {
      search.set(key, String(value));
    }
  });
  const text = search.toString();
  return text ? `?${text}` : '';
}

export interface AdjudicatePayload {
  field_key: string;
  value?: string;
  unit?: string;
  source_notice_id?: number;
}

export const projectApi = {
  list: (query: ProjectQuery = {}) =>
    http.get<ProjectList>(`/projects${toQuery(query as Record<string, string | number | undefined>)}`),
  stats: () => http.get<ProjectStats>('/projects/stats'),
  filterOptions: () => http.get<ProjectFilterOptions>('/projects/filter-options'),
  detail: (projectId: number) => http.get<ProjectDetail>(`/projects/${projectId}`),
  adjudicate: (projectId: number, payload: AdjudicatePayload) =>
    http.post<{ decision: ProjectDetail['decisions'][number]; previous_value: string | null }>(
      `/projects/${projectId}/decisions`,
      payload,
    ),
  resetDecision: (projectId: number, fieldKey: string) =>
    http.delete<{ decision: ProjectDetail['decisions'][number] }>(
      `/projects/${projectId}/decisions/${fieldKey}`,
    ),
};
