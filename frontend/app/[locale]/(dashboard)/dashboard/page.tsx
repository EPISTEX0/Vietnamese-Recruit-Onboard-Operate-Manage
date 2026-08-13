    'use client';

    /**
     * HR dashboard.
     *
     * Runtime health and the audit log used to render here, but both read
     * `/api/system-admin/*` and return 403 for HR. They belong to the system
     * admin console (/settings), which already renders both.
     */

    import React from 'react';
    import { useQuery } from '@tanstack/react-query';
    import { useTranslations } from 'next-intl';
    import {
      LayoutDashboard, TrendingUp, CheckCircle, XCircle, Clock
    } from 'lucide-react';
    import { getMetrics } from '@/lib/api/recruitment';
    import type { MetricsResponse } from '@/lib/api/recruitment';

    export default function DashboardPage() {
      const t = useTranslations('dashboard');

      // Recruitment metrics
      const { data: metrics, isLoading: metricsLoading, error: metricsError } = useQuery<MetricsResponse>({
        queryKey: ['recruitment-metrics'],
        queryFn: getMetrics,
        staleTime: 30 * 1000,
        placeholderData: (prev) => prev,
      });

      return (
        <div className="space-y-6 animate-fadeSlideIn">
          {/* Page Header */}
          <div className="flex items-center justify-between">
            <div>
              <div className="flex items-center gap-2 text-indigo-600 mb-1">
                <LayoutDashboard className="w-5 h-5" />
                <h1 className="text-xl font-bold text-slate-900">{t('title')}</h1>
              </div>
              <p className="text-sm text-slate-500">
                {t('subtitle')}
              </p>
            </div>
          </div>

          {/* Metrics Cards — Bento Grid */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {/* Recruitment Pipeline */}
            <div className="p-5 bg-white rounded-2xl border border-slate-200 shadow-sm shadow-slate-100">
              <div className="flex items-center justify-between mb-3">
                <div className="p-2 bg-indigo-50 rounded-lg">
                  <TrendingUp className="w-5 h-5 text-indigo-600" />
                </div>
                <span className="text-[10px] font-mono uppercase text-slate-400">{t('queue')}</span>
              </div>
              {metricsLoading ? (
                <div className="animate-pulse h-8 bg-slate-100 rounded w-3/4" />
              ) : metricsError ? (
                <p className="text-xs text-rose-500">{t('metricsLoadError')}</p>
              ) : (
                <>
                  <div className="text-2xl font-bold text-slate-900">{metrics?.queue_depth ?? 0}</div>
                  <p className="text-xs text-slate-500 mt-1">{t('queueDepth')}</p>
                </>
              )}
            </div>

            {/* Success Rate */}
            <div className="p-5 bg-white rounded-2xl border border-slate-200 shadow-sm shadow-slate-100">
              <div className="flex items-center justify-between mb-3">
                <div className="p-2 bg-emerald-50 rounded-lg">
                  <CheckCircle className="w-5 h-5 text-emerald-600" />
                </div>
                <span className="text-[10px] font-mono uppercase text-slate-400">{t('successRate')}</span>
              </div>
              {metricsLoading ? (
                <div className="animate-pulse h-8 bg-slate-100 rounded w-3/4" />
              ) : metricsError ? (
                <p className="text-xs text-rose-500">—</p>
              ) : (
                <>
                  <div className="text-2xl font-bold text-slate-900">
                    {metrics ? Math.round(metrics.success_rate * 100) : 0}%
                  </div>
                  <p className="text-xs text-slate-500 mt-1">{t('cvSuccess')}</p>
                </>
              )}
            </div>

            {/* Failure Rate */}
            <div className="p-5 bg-white rounded-2xl border border-slate-200 shadow-sm shadow-slate-100">
              <div className="flex items-center justify-between mb-3">
                <div className="p-2 bg-rose-50 rounded-lg">
                  <XCircle className="w-5 h-5 text-rose-600" />
                </div>
                <span className="text-[10px] font-mono uppercase text-slate-400">{t('failureRate')}</span>
              </div>
              {metricsLoading ? (
                <div className="animate-pulse h-8 bg-slate-100 rounded w-3/4" />
              ) : metricsError ? (
                <p className="text-xs text-rose-500">—</p>
              ) : (
                <>
                  <div className="text-2xl font-bold text-slate-900">
                    {metrics ? Math.round(metrics.failure_rate * 100) : 0}%
                  </div>
                  <p className="text-xs text-slate-500 mt-1">{t('cvFailed')}</p>
                </>
              )}
            </div>

            {/* Avg Processing Time */}
            <div className="p-5 bg-white rounded-2xl border border-slate-200 shadow-sm shadow-slate-100">
              <div className="flex items-center justify-between mb-3">
                <div className="p-2 bg-amber-50 rounded-lg">
                  <Clock className="w-5 h-5 text-amber-600" />
                </div>
                <span className="text-[10px] font-mono uppercase text-slate-400">{t('avgTime')}</span>
              </div>
              {metricsLoading ? (
                <div className="animate-pulse h-8 bg-slate-100 rounded w-3/4" />
              ) : metricsError ? (
                <p className="text-xs text-rose-500">—</p>
              ) : (
                <>
                  <div className="text-2xl font-bold text-slate-900">
                    {metrics ? (metrics.average_processing_time_ms / 1000).toFixed(1) : 0}s
                  </div>
                  <p className="text-xs text-slate-500 mt-1">{t('avgProcessing')}</p>
                </>
              )}
            </div>
          </div>
        </div>
      );
    }
    