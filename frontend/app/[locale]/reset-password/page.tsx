'use client';

import React, { useState, useEffect, Suspense } from 'react';
import { useSearchParams } from 'next/navigation';
import { useRouter, Link } from '@/i18n/navigation';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { Shield, AlertTriangle, Eye, EyeOff, CheckCircle, ArrowLeft, LinkIcon } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { getResetTokenInfo, resetPassword, AuthApiError } from '@/lib/api/auth';
import { errorMessage } from '@/lib/api/error-codes';
import { resetPasswordSchema, type ResetPasswordFormData } from '@/lib/api/auth-schemas';
import LocaleSwitcher from '@/components/locale-switcher';

// Backend generates the raw token with secrets.token_urlsafe(32):
// unpadded base64url. Reject anything that cannot be a real token before
// hitting the API (ADR 0010 — client-side format validation).
const RESET_TOKEN_PATTERN = /^[A-Za-z0-9_-]{20,}$/;

function ResetPasswordForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const t = useTranslations('resetPassword');
  const tAppShell = useTranslations('appShell');
  const token = searchParams.get('token');

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<ResetPasswordFormData>({
    resolver: zodResolver(resetPasswordSchema),
  });

  // loading → validating the reset token; ready → show form; invalid → show error state
  const [status, setStatus] = useState<'loading' | 'ready' | 'invalid'>('loading');
  const [statusError, setStatusError] = useState('');
  const [serverError, setServerError] = useState('');
  const [success, setSuccess] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [showNew, setShowNew] = useState(false);

  // Validate the reset token on mount
  useEffect(() => {
    let cancelled = false;
    if (!token) {
      setStatus('invalid');
      setStatusError(t('missingToken'));
      return;
    }
    if (!RESET_TOKEN_PATTERN.test(token)) {
      setStatus('invalid');
      setStatusError(t('missingToken'));
      return;
    }
    (async () => {
      try {
        const res = await getResetTokenInfo(token);
        if (!cancelled) {
          setStatus(res.valid ? 'ready' : 'invalid');
        }
      } catch (err: unknown) {
        if (!cancelled) {
          setStatus('invalid');
          setStatusError(errorMessage(err, t('error')));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token, t]);

  const onSubmit = async (data: ResetPasswordFormData) => {
    setServerError('');
    setIsSubmitting(true);
    try {
      await resetPassword(token ?? '', data.new_password);
      setSuccess(true);
      // Token is single-use; redirect to login after a short delay
      setTimeout(() => router.replace('/login'), 2000);
    } catch (err: unknown) {
      if (err instanceof AuthApiError && err.code === 'AUTH_INVALID_RESET_TOKEN') {
        // Token consumed/expired between validation and submit → show error state
        setStatus('invalid');
      } else {
        setServerError(errorMessage(err, t('error')));
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  if (status === 'loading') {
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
              <span className="font-sans font-bold text-lg tracking-tight text-slate-900 block">{tAppShell('brand')}</span>
              <span className="text-xs text-slate-500 block font-mono">{t('title')}</span>
            </div>
          </div>
          <LocaleSwitcher />
        </div>

        <div className="flex items-center gap-3 mb-6">
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
            <p className="text-xs">{t('successHint')}</p>
            <Link
              href="/login"
              className="inline-flex items-center justify-center gap-1.5 text-sm font-semibold text-indigo-600 hover:text-indigo-700"
            >
              <ArrowLeft className="w-4 h-4" />
              {t('loginNow')}
            </Link>
          </div>
        )}

        {status === 'invalid' && (
          <div className="p-4 mb-6 bg-rose-50 border border-rose-200 text-rose-700 rounded-xl text-center space-y-3">
            <AlertTriangle className="w-8 h-8 mx-auto text-rose-500" />
            <p className="font-semibold">{t('invalidTitle')}</p>
            <p className="text-xs">{statusError || t('invalidMessage')}</p>
            <div className="flex flex-col gap-2 pt-1">
              <Link
                href="/forgot-password"
                className="w-full py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white font-semibold rounded-xl flex items-center justify-center gap-2 shadow-lg shadow-indigo-100 transition-all"
              >
                <LinkIcon className="w-4 h-4" />
                {t('requestNewLink')}
              </Link>
              <Link
                href="/login"
                className="w-full py-2.5 border border-slate-200 text-slate-600 hover:text-indigo-600 hover:border-indigo-300 font-semibold rounded-xl flex items-center justify-center gap-2 transition-all"
              >
                <ArrowLeft className="w-4 h-4" />
                {t('backToLogin')}
              </Link>
            </div>
          </div>
        )}

        {serverError && (
          <div className="p-3 mb-6 bg-rose-50 border border-rose-200 text-rose-600 rounded-xl text-sm flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
            <span>{serverError}</span>
          </div>
        )}

        {status === 'ready' && !success && (
          <form onSubmit={handleSubmit(onSubmit)} className="space-y-5">
            <div>
              <label className="block text-xs font-mono uppercase text-slate-500 mb-2 font-semibold">{t('newPassword')}</label>
              <div className="relative">
                <input
                  id="reset-password-input"
                  type={showNew ? 'text' : 'password'}
                  {...register("new_password")}
                  placeholder={t('newPasswordPlaceholder')}
                  className="w-full p-3 pr-10 bg-slate-50 border border-slate-200 rounded-xl text-slate-800 placeholder-slate-400 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
                  autoComplete="new-password"
                />
                <button
                  type="button"
                  onClick={() => setShowNew(!showNew)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600"
                >
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
                id="reset-password-confirm-input"
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
              id="reset-password-submit-button"
              type="submit"
              disabled={isSubmitting}
              className="w-full py-3.5 bg-indigo-600 hover:bg-indigo-700 disabled:bg-indigo-400 text-white font-semibold rounded-xl flex items-center justify-center gap-2 shadow-lg shadow-indigo-100 transition-all"
            >
              {isSubmitting ? (
                <>
                  <div className="animate-spin w-4 h-4 border-2 border-white border-t-transparent rounded-full" />
                  {t('submitting')}
                </>
              ) : (
                <>
                  <Shield className="w-4 h-4" />
                  {t('submit')}
                </>
              )}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}

export default function ResetPasswordPage() {
  return (
    <Suspense
      fallback={
        <div className="flex items-center justify-center min-h-screen bg-slate-50">
          <div className="animate-spin w-8 h-8 border-2 border-indigo-600 border-t-transparent rounded-full" />
        </div>
      }
    >
      <ResetPasswordForm />
    </Suspense>
  );
}
