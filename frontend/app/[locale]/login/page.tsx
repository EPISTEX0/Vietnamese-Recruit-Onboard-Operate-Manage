'use client';

import React, { useState, useEffect } from 'react';
import { useRouter, Link } from '@/i18n/navigation';
import { useQueryClient } from '@tanstack/react-query';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { LogIn, AlertTriangle, Eye, EyeOff } from 'lucide-react';
import { login, AuthApiError, type CurrentUser } from '@/lib/api/auth';
import { useSession } from '@/lib/auth/session';
import { homePathForRole, isUserRole } from '@/lib/auth/roles';
import { getErrorMessage } from '@/lib/api/error-codes';
import type { ApiError } from '@/lib/api/types';
import { loginSchema, type LoginFormData } from '@/lib/api/auth-schemas';
import { useTranslations } from 'next-intl';
import LocaleSwitcher from '@/components/locale-switcher';

export default function LoginPage() {
  const router = useRouter();
  const qc = useQueryClient();
  const { isAuthenticated, role, isLoading } = useSession();
  const t = useTranslations();

  const {
    register,
    handleSubmit,
    setError,
    formState: { errors },
  } = useForm<LoginFormData>({
    resolver: zodResolver(loginSchema),
  });

  const [serverError, setServerError] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [showPassword, setShowPassword] = useState(false);

  // Redirect if already authenticated
  useEffect(() => {
    if (!isLoading && isAuthenticated && role) {
      router.replace(homePathForRole(role));
    }
  }, [isLoading, isAuthenticated, role, router]);

  const onSubmit = async (data: LoginFormData) => {
    setServerError('');
    setIsSubmitting(true);
    try {
      const result = await login(data.email.trim(), data.password);
      // BUG-10 fix: BE set HttpOnly auth cookie bằng login response, nhưng cookie
      // KHÔNG đọc được từ JS (HttpOnly). useSession() chỉ biết user qua GET /api/auth/me
      // — mà React Query cache ['session'] vãn giư giá trị STALE null từ lần /me 401
      // khi /login mới mount (chưa có cookie). Nếu không sync cache ngay, các trang
      // protected (vd /change-password) thấy isAuthenticated=false (stale) → redirect
      // /login nhầm → race chặn cascade ESS. Login response body đã chứa user đầy đủ,
      // dụng nó cập nhật cache ngay.
      qc.setQueryData<CurrentUser | null>(['session'], result.user);
      // BE sets HttpOnly cookie; session data in response
      if (result.must_change_password) {
        router.replace('/change-password'); // locale-aware via @/i18n/navigation
      } else {
        // locale-aware via @/i18n/navigation; each role has its own home
        router.replace(isUserRole(result.user.role) ? homePathForRole(result.user.role) : '/');
      }
    } catch (err: unknown) {
      if (err instanceof AuthApiError) {
        // Map BE field-level errors to form fields
        if (Object.keys(err.fields).length > 0) {
          for (const [field, msg] of Object.entries(err.fields)) {
            setError(field as keyof LoginFormData, { message: msg });
          }
          setServerError(t('login.checkInfo'));
        } else {
          const code = err.code;
          const msg = getErrorMessage(code ?? '') || err.message || t('login.error');
          setServerError(msg);
        }
      } else if (err instanceof Error) {
        const code = (err as ApiError).errorCode;
        const msg = getErrorMessage(code ?? '') || err.message || t('login.error');
        setServerError(msg);
      } else {
        setServerError(t('login.error'));
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
        {/* Brand Banner */}
        <div className="flex items-center justify-between mb-8">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-gradient-to-tr from-indigo-600 to-indigo-500 rounded-lg text-white font-black tracking-tighter text-xl shadow-md shadow-indigo-100">
              VR
            </div>
            <div>
              <span className="font-sans font-bold text-lg tracking-tight text-slate-900 block">{t('appShell.brand')}</span>
              <span className="text-xs text-slate-500 block font-mono">{t('login.title')}</span>
            </div>
          </div>
          <LocaleSwitcher />
        </div>

        {serverError && (
          <div className="p-3 mb-6 bg-rose-50 border border-rose-200 text-rose-600 rounded-xl text-sm flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
            <span>{serverError}</span>
          </div>
        )}

        <form onSubmit={handleSubmit(onSubmit)} className="space-y-5">
          <div>
            <label className="block text-xs font-mono uppercase text-slate-500 mb-2 font-semibold">{t('login.email')}</label>
            <input
              id="login-email-input"
              type="email"
              {...register("email")}
              placeholder={t('login.placeholderEmail')}
              className="w-full p-3 bg-slate-50 border border-slate-200 rounded-xl text-slate-800 placeholder-slate-400 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
              autoComplete="email"
            />
            {errors.email && (
              <p className="mt-1.5 text-xs text-rose-500">{errors.email.message}</p>
            )}
          </div>

          <div>
            <label className="block text-xs font-mono uppercase text-slate-500 mb-2 font-semibold">{t('login.password')}</label>
            <div className="relative">
              <input
                id="login-password-input"
                type={showPassword ? 'text' : 'password'}
                {...register("password")}
                placeholder={t('login.placeholderPassword')}
                className="w-full p-3 pr-10 bg-slate-50 border border-slate-200 rounded-xl text-slate-800 placeholder-slate-400 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
                autoComplete="current-password"
              />
              <button
                type="button"
                onClick={() => setShowPassword(!showPassword)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600"
              >
                {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
            {errors.password && (
              <p className="mt-1.5 text-xs text-rose-500">{errors.password.message}</p>
            )}
          </div>

          <button
            id="login-submit-button"
            type="submit"
            disabled={isSubmitting}
            className="w-full py-3.5 bg-indigo-600 hover:bg-indigo-700 disabled:bg-indigo-400 text-white font-semibold rounded-xl flex items-center justify-center gap-2 shadow-lg shadow-indigo-100 transition-all"
          >
            {isSubmitting ? (
              <>
                <div className="animate-spin w-4 h-4 border-2 border-white border-t-transparent rounded-full" />
                {t('login.loggingIn')}
              </>
            ) : (
              <>
                <LogIn className="w-4 h-4" />
                {t('login.submit')}
              </>
            )}
          </button>
        </form>

        <div className="mt-5 text-center">
          <Link
            href="/forgot-password"
            className="text-sm text-slate-500 hover:text-indigo-600 transition-colors"
          >
            {t('login.forgotPassword')}
          </Link>
        </div>
      </div>
    </div>
  );
}
