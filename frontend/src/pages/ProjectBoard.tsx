import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { projectApi } from '../api/projects';
import EmptyState from '../components/common/EmptyState';
import MetricStrip from '../components/common/MetricStrip';
import PageHead from '../components/common/PageHead';
import Stamp from '../components/common/Stamp';
import { useAsync } from '../hooks/useAsync';
import { useAuth } from '../router/guards';
import { can } from '../constants/roles';
import { PROJECT_STATUS_LABEL, REGIONS, CATEGORIES } from '../constants/enums';
import { errorMessage } from '../utils/errorMessage';
import { fmtNumber, fmtScore } from '../utils/format';
import type { ProjectSummary } from '../types/models';

type ConflictFilter = '' | 'conflict' | 'pending';

export default function ProjectBoard() {
  const navigate = useNavigate();
  const auth = useAuth();
  const canAdjudicate = can(auth.user?.role, 'project:adjudicate');
  const [query, setQuery] = useState<{
    q: string;
    region: string;
    category: string;
    status: string;
    conflict: ConflictFilter;
  }>({ q: '', region: '', category: '', status: '', conflict: '' });
  const [keyword, setKeyword] = useState('');
  const [page, setPage] = useState(1);

  const stats = useAsync(() => projectApi.stats(), []);
  const list = useAsync(
    () =>
      projectApi.list({
        q: query.q || undefined,
        region: query.region || undefined,
        category: query.category || undefined,
        status: query.status || undefined,
        conflict: query.conflict || undefined,
        page,
        page_size: 25,
      }),
    [query, page],
  );

  const s = stats.data;

  return (
    <div data-testid="project-board">
      <PageHead
        kicker="PROJECT LEDGER · 跨来源项目归并"
        title="项目归并登记簿"
        sub={
          <>
            同一招标项目在多个交易中心的转发归并为一条 · 保留各来源与字段裁决依据 ·
            人工确认后采集不再覆盖
          </>
        }
        refCode="BID-PROJECT-2026"
        actions={
          canAdjudicate ? (
            <span className="readonly-hint mono">可裁定冲突 / 待补字段</span>
          ) : (
            <span className="readonly-hint mono">只读角色 · 不可裁定</span>
          )
        }
      />

      <MetricStrip
        cells={[
          { key: 'total', label: '归并项目', value: s?.project_total ?? '—', unit: '个', hint: '跨来源去重后' },
          { key: 'active', label: '跟踪中', value: s?.active_total ?? '—', unit: '个' },
          {
            key: 'withdrawn',
            label: '已撤回',
            value: s?.withdrawn_total ?? '—',
            unit: '个',
            hint: '全部来源撤下',
          },
          {
            key: 'conflict',
            label: '冲突待裁定',
            value: s?.conflict_field_total ?? '—',
            unit: '字段',
            accent: true,
            hint: '来源取值不一致',
          },
          {
            key: 'pending',
            label: '待补全字段',
            value: s?.pending_field_total ?? '—',
            unit: '字段',
            hint: '各来源均缺失',
          },
          { key: 'manual', label: '人工确认', value: s?.manual_field_total ?? '—', unit: '字段' },
        ]}
      />

      <section className="panel">
        <div className="panel-head">
          <h2>归并项目 · 全部</h2>
          <span className="meta mono">{list.data ? `${list.data.total} 个项目` : '加载中'}</span>
        </div>
        <div className="filter-bar">
          <form
            className="filter-inline"
            onSubmit={(event) => {
              event.preventDefault();
              setPage(1);
              setQuery((prev) => ({ ...prev, q: keyword.trim() }));
            }}
          >
            <input
              className="input"
              placeholder="按项目名称搜索"
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
            />
            <button className="btn btn-sm" type="submit">
              检索
            </button>
          </form>
          <select
            className="input"
            value={query.status}
            onChange={(event) => {
              setPage(1);
              setQuery((prev) => ({ ...prev, status: event.target.value }));
            }}
          >
            <option value="">全部状态</option>
            <option value="active">跟踪中</option>
            <option value="withdrawn">已撤回</option>
          </select>
          <select
            className="input"
            value={query.conflict}
            onChange={(event) => {
              setPage(1);
              setQuery((prev) => ({ ...prev, conflict: event.target.value as ConflictFilter }));
            }}
          >
            <option value="">裁决状态不限</option>
            <option value="conflict">仅看来源冲突</option>
            <option value="pending">仅看待补全</option>
          </select>
          <select
            className="input"
            value={query.region}
            onChange={(event) => {
              setPage(1);
              setQuery((prev) => ({ ...prev, region: event.target.value }));
            }}
          >
            <option value="">全部区域</option>
            {REGIONS.map((region) => (
              <option key={region} value={region}>
                {region}
              </option>
            ))}
          </select>
          <select
            className="input"
            value={query.category}
            onChange={(event) => {
              setPage(1);
              setQuery((prev) => ({ ...prev, category: event.target.value }));
            }}
          >
            <option value="">全部分类</option>
            {CATEGORIES.map((category) => (
              <option key={category} value={category}>
                {category}
              </option>
            ))}
          </select>
        </div>

        {list.loading ? (
          <div className="panel-loading">正在归并项目 …</div>
        ) : list.error ? (
          <EmptyState
            title="项目登记簿没能打开"
            hint={errorMessage(list.error, '请检查后端服务是否就绪')}
            actionLabel="重新加载"
            onAction={list.reload}
          />
        ) : !list.data || list.data.items.length === 0 ? (
          <EmptyState title="没有匹配的项目" hint="调整筛选条件，或先跑一次采集批次把跨来源副本归并进来。" />
        ) : (
          <>
            <table className="data ledger">
              <thead>
                <tr>
                  <th>项目名称</th>
                  <th className="num">来源数</th>
                  <th>区域 / 分类</th>
                  <th className="num">冲突字段</th>
                  <th className="num">待补字段</th>
                  <th>截止风险</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                {list.data.items.map((project: ProjectSummary) => (
                  <tr
                    key={project.id}
                    className="clickable"
                    onClick={() => navigate(`/projects/${project.id}`)}
                  >
                    <td className="wrap">
                      <span className="notice-title">{project.display_name}</span>
                      {project.conflict_count > 0 ? (
                        <Stamp tone="seal" rotate={2}>
                          {project.conflict_count} 冲突
                        </Stamp>
                      ) : null}
                      {project.pending_field_count > 0 ? (
                        <Stamp tone="brass" rotate={-1}>
                          {project.pending_field_count} 待补
                        </Stamp>
                      ) : null}
                    </td>
                    <td className="num mono">{project.active_source_count}</td>
                    <td>
                      {project.region} · {project.category}
                    </td>
                    <td className="num mono">{project.conflict_count}</td>
                    <td className="num mono">{project.pending_field_count}</td>
                    <td className="mono small">
                      {project.risk_level ? (
                        <>
                          <span className={`conf-min mono${project.risk_level === 'must_act' ? ' low' : ''}`}>
                            {project.risk_level}
                          </span>
                          <span className="conf-sub"> {fmtScore(project.risk_score ?? 0)}</span>
                        </>
                      ) : (
                        <span className="faint">未入日历</span>
                      )}
                    </td>
                    <td>
                      <Stamp tone={project.status === 'withdrawn' ? 'faint' : 'seal'}>
                        {PROJECT_STATUS_LABEL[project.status]}
                      </Stamp>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="pager">
              <button
                className="btn btn-sm btn-ghost"
                type="button"
                onClick={() => setPage((value) => Math.max(1, value - 1))}
                disabled={page <= 1}
              >
                上一页
              </button>
              <span className="mono">
                第 {list.data.page} 页 / 共 {Math.max(1, Math.ceil(list.data.total / list.data.page_size))} 页
              </span>
              <button
                className="btn btn-sm btn-ghost"
                type="button"
                onClick={() => setPage((value) => value + 1)}
                disabled={page >= Math.ceil(list.data.total / list.data.page_size)}
              >
                下一页
              </button>
            </div>
            <div className="panel-foot mono">
              来源数 = 当前有效采源数量；冲突/待补字段进入项目详情裁定，人工确认后的字段不会被后续采集覆盖。
              在库预算口径合计 {fmtNumber(s?.project_total ?? 0, 0)} 个项目。
            </div>
          </>
        )}
      </section>
    </div>
  );
}
