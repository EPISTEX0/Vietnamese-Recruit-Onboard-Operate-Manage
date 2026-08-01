'use client';

import React, { useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { KeyRound, AlertTriangle, CheckCircle, ArrowLeft } from 'lucide-react';
import { Link } from '@/i18n/navigation';
import { useTranslations } from 'next-intl';
import { forgotPassword } from '@/lib/api/auth';
import { errorMessage } from '@/lib/api/error-codes';
import { forgotPasswordSchema, type ForgotPasswordFormData } from '@/lib/api/auth-schemas';
import LocaleSwitcher from '@/components/locale-switcher';

export default function ForgotPasswordPage() {
  const t = useTranslations('forgotPassword');
  const tAppShell = useTranslations('appShell');

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<ForgotPasswordFormData>({
    resolver: zodResolver(forgotPasswordSchema),
  });

  const [serverError, setServerError] = useState('');
  const [success, setSuccess] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const onSubmit = async (data: ForgotPasswordFormData) => {
    setServerError('');
    setIsSubmitting(true);
    try {
      // BE always returns the same generic message (anti-enumeration),
      // so the success box is rendered from i18n regardless of the payload.
      await forgotPassword(data.email.trim());
      setSuccess(true);
    } catch (err: unknown) {
      setServerError(errorMessage(err, t('error')));
    } finally {
      setIsSubmitting(false);
    }
  };

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
            <KeyRound className="w-6 h-6 text-indigo-600" />
          </div>
          <div>
            <h1 className="font-bold text-lg text-slate-900">{t('title')}</h1>
            <p className="text-xs text-slate-500">{t('subtitle')}</p>
          </div>
        </div>

        {success && (
          <div className="p-4 mb-6 bg-emerald-50 border border-emerald-200 text-emerald-700 rounded-xl text-center space-y-2">
            <CheckCircle className="w-8 h-8 mx-auto text-emerald-500" />
            <p className="font-semibold">{t('successTitle')}</p>
            <p className="text-xs">{t('success')}</p>
            <Link
              href="/login"
              className="inline-flex items-center gap-1.5 text-sm font-semibold text-indigo-600 hover:text-indigo-700"
            >
              <ArrowLeft className="w-4 h-4" />
              {t('backToLogin')}
            </Link>
          </div>
        )}

        {serverError && (
          <div className="p-3 mb-6 bg-rose-50 border border-rose-200 text-rose-600 rounded-xl text-sm flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
            <span>{serverError}</span>
          </div>
        )}

        {!success && (
          <>
            <form onSubmit={handleSubmit(onSubmit)} className="space-y-5">
              <div>
                <label className="block text-xs font-mono uppercase text-slate-500 mb-2 font-semibold">{t('email')}</label>
                <input
                  id="forgot-password-email-input"
                  type="email"
                  {...register("email")}
                  placeholder={t('placeholderEmail')}
                  className="w-full p-3 bg-slate-50 border border-slate-200 rounded-xl text-slate-800 placeholder-slate-400 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
                  autoComplete="email"
                />
                {errors.email && (
                  <p className="mt-1.5 text-xs text-rose-500">{errors.email.message}</p>
                )}
              </div>

              <button
                id="forgot-password-submit-button"
                type="submit"
                disabled={isSubmitting}
                className="w-full py-3.5 bg-indigo-600 hover:bg-indigo-700 disabled:bg-indigo-400 text-white font-semibold rounded-xl flex items-center justify-center gap-2 shadow-lg shadow-indigo-100 transition-all"
              >
                {isSubmitting ? (
                  <>
                    <div className="animate-spin w-4 h-4 border-2 border-white border-t-transparent rounded-full" />
                    {t('sending')}
                  </>
                ) : (
                  <>
                    <KeyRound className="w-4 h-4" />
                    {t('submit')}
                  </>
                )}
              </button>
            </form>

            <div className="mt-5 text-center">
              <Link
                href="/login"
                className="inline-flex items-center gap-1.5 text-sm text-slate-500 hover:text-indigo-600 transition-colors"
              >
                <ArrowLeft className="w-4 h-4" />
                {t('backToLogin')}
              </Link>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
