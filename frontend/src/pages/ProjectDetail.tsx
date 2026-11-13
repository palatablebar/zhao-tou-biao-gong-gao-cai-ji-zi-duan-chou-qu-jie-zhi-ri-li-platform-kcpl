import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { projectApi } from '../api/projects';
import EmptyState from '../components/common/EmptyState';
import PageHead from '../components/common/PageHead';
import Stamp from '../components/common/Stamp';
import { useAsync } from '../hooks/useAsync';
import { useAuth } from '../router/guards';
import { can } from '../constants/roles';
import {
  DECISION_BASIS_LABEL,
  DECISION_STATUS_LABEL,
  FIELD_LABEL,
  VERSION_STATUS_LABEL,
} from '../constants/enums';
import { uiStore } from '../stores/uiStore';
import { errorMessage } from '../utils/errorMessage';
import { fmtDateTime, fmtNumber, fmtScore } from '../utils/format';
import type { ProjectDecision, ProjectSourceItem } from '../types/models';

const DECISION_TONE: Record<string, 'seal' | 'ink' | 'brass' | 'faint'> = {
  auto: 'ink',
  manual: 'seal',
  conflict: 'seal',
  pending: 'brass',
};

export default function ProjectDetailPage() {
  const { projectId } = useParams();
  const id = Number(projectId);
  const navigate = useNavigate();
  const auth = useAuth();
  const canAdjudicate = can(auth.user?.role, 'project:adjudicate');
  const detail = useAsync(() => projectApi.detail(id), [id]);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [manualValue, setManualValue] = useState<Record<string, string>>({});

  async function chooseCandidate(decision: ProjectDecision, noticeId: number) {
    setBusyKey(decision.field_key);
    try {
      await projectApi.adjudicate(id, {
        field_key: decision.field_key,
        source_notice_id: noticeId,
      });
      uiStore.push('ok', `已人工确认「${decision.label}」的来源取值，后续采集不再覆盖`);
      detail.reload();
    } catch (error) {
      uiStore.push('error', errorMessage(error, '裁定失败'));
    } finally {
      setBusyKey(null);
    }
  }

  async function submitManualValue(decision: ProjectDecision) {
    const value = (manualValue[decision.field_key] || '').trim();
    if (!value) {
      uiStore.push('warn', '请先填写裁定值');
      return;
    }
    setBusyKey(decision.field_key);
    try {
      await projectApi.adjudicate(id, { field_key: decision.field_key, value });
      uiStore.push('ok', `已人工修正「${decision.label}」，后续采集不再覆盖`);
      setManualValue((prev) => ({ ...prev, [decision.field_key]: '' }));
      detail.reload();
    } catch (error) {
      uiStore.push('error', errorMessage(error, '裁定失败'));
    } finally {
      setBusyKey(null);
    }
  }

  async function resetDecision(decision: ProjectDecision) {
    setBusyKey(decision.field_key);
    try {
      await projectApi.resetDecision(id, decision.field_key);
      uiStore.push('ok', `已撤销「${decision.label}」人工裁定，恢复系统自动裁决`);
      detail.reload();
    } catch (error) {
      uiStore.push('error', errorMessage(error, '撤销失败'));
    } finally {
      setBusyKey(null);
    }
  }

  if (detail.loading) {
    return <div className="panel-loading">正在调阅项目卷宗 …</div>;
  }
  if (detail.error || !detail.data) {
    return (
      <EmptyState
        title="项目卷宗没能打开"
        hint={errorMessage(detail.error)}
        actionLabel="重新加载"
        onAction={detail.reload}
      />
    );
  }

  const project = detail.data;

  return (
    <div data-testid="project-detail">
      <PageHead
        kicker="PROJECT DOSSIER · 项目归并卷宗"
        title={project.display_name}
        sub={
          <>
            {project.region} · {project.category} · {project.industry} · 招标人 {project.publisher}
          </>
        }
        refCode={`PRJ-${String(project.id).padStart(4, '0')}`}
        actions={
          <button className="btn btn-sm btn-ghost" type="button" onClick={() => navigate('/projects')}>
            返回登记簿
          </button>
        }
      />

      <section className="panel">
        <div className="panel-head">
          <h2>归并概览</h2>
          <Stamp tone={project.status === 'withdrawn' ? 'faint' : 'seal'}>
            {project.status_label}
          </Stamp>
        </div>
        <div className="detail-grid">
          <div className="detail-meta">
            <span className="meta mono">有效来源</span>
            <strong className="mono">{project.active_source_count}</strong>
          </div>
          <div className="detail-meta">
            <span className="meta mono">冲突字段</span>
            <strong className="mono">{project.conflict_count}</strong>
          </div>
          <div className="detail-meta">
            <span className="meta mono">待补字段</span>
            <strong className="mono">{project.pending_field_count}</strong>
          </div>
          <div className="detail-meta">
            <span className="meta mono">最近出现</span>
            <strong className="mono">{fmtDateTime(project.last_seen_at)}</strong>
          </div>
          {project.withdrawn_at ? (
            <div className="detail-meta">
              <span className="meta mono">撤回时间</span>
              <strong className="mono">{fmtDateTime(project.withdrawn_at)}</strong>
            </div>
          ) : null}
        </div>
        {project.status === 'withdrawn' ? (
          <div className="panel-foot mono">
            该项目全部来源均已撤回，截止日历事件已停用并从本周必投名单移除；历史正文与版本仍保留可查。
          </div>
        ) : null}
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>各来源登记与版本链</h2>
          <span className="meta mono">{project.sources.length} 个来源</span>
        </div>
        <div className="field-list">
          {project.sources.map((source: ProjectSourceItem) => (
            <div className="field" key={source.id}>
              <div className="field-row">
                <div className="field-row-head">
                  <strong>{source.site_name}</strong>
                  <span className="mono faint">{source.site_domain}</span>
                  <Stamp tone={source.status === 'active' ? 'ink' : 'faint'}>
                    {source.status === 'active' ? '有效' : source.status === 'withdrawn' ? '已撤下' : '已替代'}
                  </Stamp>
                </div>
                <div className="field-facts mono small">
                  登记号 {source.external_id} · 首次出现 {fmtDateTime(source.first_seen_at)} · 最近抓取{' '}
                  {fmtDateTime(source.last_seen_at)}
                  {source.withdrawn_at ? ` · 撤下 ${fmtDateTime(source.withdrawn_at)}` : ''}
                </div>
                <div className="version-chain">
                  {source.versions.map((version) => (
                    <span
                      key={version.version_no}
                      className={`version-chip version-${version.status}`}
                      title={version.change_note || ''}
                    >
                      v{version.version_no} · {VERSION_STATUS_LABEL[version.status]}
                      {version.change_note ? <em className="mono">（{version.change_note}）</em> : null}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>字段裁决与来源依据</h2>
          <span className="meta mono">人工确认优先级最高，采集只更新候选不覆盖裁决</span>
        </div>
        <div className="field-list">
          {project.decisions.map((decision) => (
            <div
              className={`field${decision.conflict ? ' field-row-low' : ''}`}
              key={decision.id}
            >
              <div className="field-row">
                <div className="field-row-head">
                  <strong>{FIELD_LABEL[decision.field_key] ?? decision.label}</strong>
                  <Stamp tone={DECISION_TONE[decision.status]}>{DECISION_STATUS_LABEL[decision.status]}</Stamp>
                  <span className="mono faint">依据：{DECISION_BASIS_LABEL[decision.basis] ?? decision.basis}</span>
                </div>

                <div className="field-facts">
                  <span className="value mono">
                    {decision.value_text || '—'}
                    {decision.amount_wan ? `（${fmtNumber(decision.amount_wan, 2)} 万元）` : ''}
                  </span>
                  <span className="mono small faint">
                    置信度 {fmtScore(decision.confidence)}
                    {decision.reviewer ? ` · 裁定人 ${decision.reviewer}` : ''}
                  </span>
                </div>
                <div className="reason small">{decision.reason}</div>

                <table className="data compact candidate-table">
                  <thead>
                    <tr>
                      <th>来源</th>
                      <th>取值</th>
                      <th className="num">置信度</th>
                      <th>发布时间</th>
                      <th>来源状态</th>
                      {canAdjudicate ? <th>操作</th> : null}
                    </tr>
                  </thead>
                  <tbody>
                    {decision.candidates.length === 0 ? (
                      <tr>
                        <td colSpan={canAdjudicate ? 6 : 5} className="faint">
                          各来源均未抽取到该字段
                        </td>
                      </tr>
                    ) : (
                      decision.candidates.map((candidate) => {
                        const isChosen = candidate.notice_id === decision.source_notice_id;
                        return (
                          <tr key={`${candidate.notice_id}-${candidate.site_id}`}>
                            <td className="small">
                              {candidate.site_name}
                              <span className="mono faint"> · {candidate.external_id}</span>
                              {isChosen ? <Stamp tone="seal">当前采用</Stamp> : null}
                            </td>
                            <td className="wrap small mono">{candidate.value_text || '（空值/占位）'}</td>
                            <td className={`num mono ${candidate.confidence < 0.55 ? 'conf-low' : ''}`}>
                              {fmtScore(candidate.confidence)}
                            </td>
                            <td className="mono small">{fmtDateTime(candidate.published_at)}</td>
                            <td className="small">
                              {candidate.notice_status === 'withdrawn' ? (
                                <Stamp tone="faint">已撤下</Stamp>
                              ) : (
                                <Stamp tone="ink">有效</Stamp>
                              )}
                            </td>
                            {canAdjudicate ? (
                              <td>
                                <button
                                  className="btn btn-sm"
                                  type="button"
                                  disabled={busyKey === decision.field_key || !candidate.value_text}
                                  onClick={() => chooseCandidate(decision, candidate.notice_id)}
                                >
                                  采用此值
                                </button>
                              </td>
                            ) : null}
                          </tr>
                        );
                      })
                    )}
                  </tbody>
                </table>

                {canAdjudicate ? (
                  <div className="correct-form">
                    <input
                      className="input"
                      placeholder="人工直接输入裁定值（金额带万元/元，日期用 YYYY-MM-DD HH:MM）"
                      value={manualValue[decision.field_key] || ''}
                      onChange={(event) =>
                        setManualValue((prev) => ({ ...prev, [decision.field_key]: event.target.value }))
                      }
                    />
                    <button
                      className="btn btn-sm btn-primary"
                      type="button"
                      disabled={busyKey === decision.field_key}
                      onClick={() => submitManualValue(decision)}
                    >
                      人工修正
                    </button>
                    {decision.status === 'manual' ? (
                      <button
                        className="btn btn-sm btn-ghost"
                        type="button"
                        disabled={busyKey === decision.field_key}
                        onClick={() => resetDecision(decision)}
                      >
                        撤销人工裁定
                      </button>
                    ) : null}
                  </div>
                ) : null}
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>截止日历事件</h2>
          <span className="meta mono">撤回项目事件停用，不进必投名单</span>
        </div>
        <table className="data compact">
          <thead>
            <tr>
              <th>类型</th>
              <th>事件时间</th>
              <th className="num">风险分</th>
              <th>状态</th>
              <th>说明</th>
            </tr>
          </thead>
          <tbody>
            {project.deadlines.length === 0 ? (
              <tr>
                <td colSpan={5} className="faint">
                  该项目暂无跟踪的截止事件
                </td>
              </tr>
            ) : (
              project.deadlines.map((event) => (
                <tr key={event.id}>
                  <td className="mono small">{event.kind}</td>
                  <td className="mono small">{fmtDateTime(event.event_at)}</td>
                  <td className="num mono">{event.risk_score ?? '—'}</td>
                  <td>
                    <Stamp tone={event.is_active ? 'ink' : 'faint'}>
                      {event.is_active ? '有效' : '已停用'}
                    </Stamp>
                  </td>
                  <td className="wrap small">{event.inactive_reason || event.note || '—'}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </section>
    </div>
  );
}
