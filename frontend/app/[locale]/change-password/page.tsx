'use client';

import React, { useState, useEffect } from 'react';
import { useRouter } from '@/i18n/navigation';
import { useQueryClient } from '@tanstack/react-query';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { Shield, AlertTriangle, Eye, EyeOff, CheckCircle } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { changePassword, AuthApiError, type CurrentUser } from '@/lib/api/auth';
import { useSession } from '@/lib/auth/session';
import { homePathForRole, isUserRole } from '@/lib/auth/roles';
import { getErrorMessage } from '@/lib/api/error-codes';
import type { ApiError } from '@/lib/api/types';
import { changePasswordSchema, type ChangePasswordFormData } from '@/lib/api/auth-schemas';

export default function ChangePasswordPage() {
  const router = useRouter();
  const qc = useQueryClient();
  const t = useTranslations('changePassword');
  const { isAuthenticated, isLoading } = useSession();

  const {
    register,
    handleSubmit,
    setError,
    formState: { errors },
  } = useForm<ChangePasswordFormData>({
    resolver: zodResolver(changePasswordSchema),
  });

  const [serverError, setServerError] = useState('');
  const [success, setSuccess] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [showCurrent, setShowCurrent] = useState(false);
  const [showNew, setShowNew] = useState(false);

  // Redirect if not authenticated
  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      router.replace('/login');
    }
  }, [isLoading, isAuthenticated, router]);

  const onSubmit = async (data: ChangePasswordFormData) => {
    setServerError('');
    setSuccess(false);
    setIsSubmitting(true);
    try {
      const result = await changePassword(data.current_password, data.new_password);
      qc.setQueryData<CurrentUser | null>(['session'], result.user);
      setSuccess(true);
      const target = isUserRole(result.user.role) ? homePathForRole(result.user.role) : '/';
      setTimeout(() => router.replace(target), 2000);
    } catch (err: unknown) {
      if (err instanceof AuthApiError) {
        if (Object.keys(err.fields).length > 0) {
          for (const [field, msg] of Object.entries(err.fields)) {
            setError(field as keyof ChangePasswordFormData, { message: msg });
          }
          setServerError(t('subtitle'));
        } else {
          const code = err.code;
          const msg = getErrorMessage(code ?? '') || err.message || t('subtitle');
          setServerError(msg);
        }
      } else if (err instanceof Error) {
        const code = (err as ApiError).errorCode;
        const msg = getErrorMessage(code ?? '') || err.message || t('subtitle');
        setServerError(msg);
      } else {
        setServerError(t('subtitle'));
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-slate-50">
        <div className="animate-spin w-8 h-8 border-2 border-indigo-600 border-t-transparent rounded-full" />
      </div>
    );
  }

  return (
    <div className="flex items-center justify-center min-h-screen p-4 bg-slate-50 text-slate-900 relative overflow-hidden">
      <div className="absolute top-[-20%] left-[-20%] w-[60%] h-[60%] rounded-full bg-indigo-50/40 blur-[120px] pointer-events-none" />
      <div className="absolute bottom-[-20%] right-[-20%] w-[60%] h-[60%] rounded-full bg-indigo-100/30 blur-[120px] pointer-events-none" />

      <div className="w-full max-w-md p-8 bg-white/95 backdrop-blur-xl rounded-2xl border border-slate-200/80 shadow-xl shadow-slate-100 relative z-10">
        <div className="flex items-center gap-3 mb-8">
          <div className="p-2.5 bg-indigo-50 rounded-xl">
            <Shield className="w-6 h-6 text-indigo-600" />
          </div>
          <div>
            <h1 className="font-bold text-lg text-slate-900">{t('title')}</h1>
            <p className="text-xs text-slate-500">{t('subtitle')}</p>
          </div>
        </div>

        {success && (
          <div className="p-4 mb-6 bg-emerald-50 border border-emerald-200 text-emerald-700 rounded-xl text-center space-y-2">
            <CheckCircle className="w-8 h-8 mx-auto text-emerald-500" />
            <p className="font-semibold">{t('success')}</p>
            <p className="text-xs">{t('redirecting')}</p>
          </div>
        )}

        {serverError && (
          <div className="p-3 mb-6 bg-rose-50 border border-rose-200 text-rose-600 rounded-xl text-sm flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
            <span>{serverError}</span>
          </div>
        )}

        {!success && (
          <form onSubmit={handleSubmit(onSubmit)} className="space-y-5">
            <div>
              <label className="block text-xs font-mono uppercase text-slate-500 mb-2 font-semibold">{t('currentPassword')}</label>
              <div className="relative">
                <input
                  type={showCurrent ? 'text' : 'password'}
                  {...register("current_password")}
                  placeholder={t('currentPasswordPlaceholder')}
                  className="w-full p-3 pr-10 bg-slate-50 border border-slate-200 rounded-xl text-slate-800 placeholder-slate-400 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
                  autoComplete="current-password"
                />
                <button type="button" onClick={() => setShowCurrent(!showCurrent)} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600">
                  {showCurrent ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
              {errors.current_password && (
                <p className="mt-1.5 text-xs text-rose-500">{errors.current_password.message}</p>
              )}
            </div>

            <div>
              <label className="block text-xs font-mono uppercase text-slate-500 mb-2 font-semibold">{t('newPassword')}</label>
              <div className="relative">
                <input
                  type={showNew ? 'text' : 'password'}
                  {...register("new_password")}
                  placeholder={t('newPasswordPlaceholder')}
                  className="w-full p-3 pr-10 bg-slate-50 border border-slate-200 rounded-xl text-slate-800 placeholder-slate-400 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
                  autoComplete="new-password"
                />
                <button type="button" onClick={() => setShowNew(!showNew)} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600">
                  {showNew ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
              {errors.new_password && (
                <p className="mt-1.5 text-xs text-rose-500">{errors.new_password.message}</p>
              )}
            </div>

            <div>
              <label className="block text-xs font-mono uppercase text-slate-500 mb-2 font-semibold">{t('confirmPassword')}</label>
              <input
                type="password"
                {...register("confirm_password")}
                placeholder={t('confirmPasswordPlaceholder')}
                className="w-full p-3 bg-slate-50 border border-slate-200 rounded-xl text-slate-800 placeholder-slate-400 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
                autoComplete="new-password"
              />
              {errors.confirm_password && (
                <p className="mt-1.5 text-xs text-rose-500">{errors.confirm_password.message}</p>
              )}
            </div>

            <button
              type="submit"
              disabled={isSubmitting}
              className="w-full py-3.5 bg-indigo-600 hover:bg-indigo-700 disabled:bg-indigo-400 text-white font-semibold rounded-xl flex items-center justify-center gap-2 shadow-lg shadow-indigo-100 transition-all"
            >
              {isSubmitting ? (
                <>
                  <div className="animate-spin w-4 h-4 border-2 border-white border-t-transparent rounded-full" />
                  {t('updating')}
                </>
              ) : (
                <>
                  <Shield className="w-4 h-4" />
                  {t('update')}
                </>
              )}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
