'use client';

import React, { useState, useEffect } from 'react';
import { useRouter } from '@/i18n/navigation';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { useTranslations } from 'next-intl';
import { Shield, Building, User, CheckCircle, AlertTriangle, ArrowRight } from 'lucide-react';
import { getSetupStatus, setupFirstRun, AuthApiError } from '@/lib/api/auth';
import { getErrorMessage } from '@/lib/api/error-codes';
import { useSession } from '@/lib/auth/session';
import { homePathForRole } from '@/lib/auth/roles';
import { setupSchema, type SetupFormData } from '@/lib/api/auth-schemas';

export default function SetupPage() {
      const router = useRouter();
      const t = useTranslations('setup');
      const { isAuthenticated, role, setupComplete, isLoading: sessionLoading } = useSession();

  const {
    register,
    handleSubmit,
    trigger,
    getValues,
    setError,
    formState: { errors },
  } = useForm<SetupFormData>({
    resolver: zodResolver(setupSchema),
  });

  const [step, setStep] = useState<1 | 2 | 3 | 4>(1);
  const [serverError, setServerError] = useState('');
  const [backendStatus, setBackendStatus] = useState<'loading' | 'available' | 'unavailable'>('loading');
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Check setup status on mount
  useEffect(() => {
    async function checkStatus() {
      try {
        const status = await getSetupStatus();
        if (status.setup_complete) {
          // Already set up — redirect accordingly
          if (isAuthenticated && role) {
            router.replace(homePathForRole(role));
          } else {
            router.replace('/login');
          }
          return;
        }
        setBackendStatus('available');
      } catch {
        setBackendStatus('unavailable');
      }
    }
    if (!sessionLoading) {
      checkStatus();
    }
  }, [sessionLoading, isAuthenticated, role, router]);

  // If already set up and authenticated, redirect
  useEffect(() => {
    if (!sessionLoading && setupComplete && isAuthenticated && role) {
      router.replace(homePathForRole(role));
    }
  }, [sessionLoading, setupComplete, isAuthenticated, role, router]);

  const handleNextStep1 = async (e: React.FormEvent) => {
    e.preventDefault();
    setServerError('');
    const valid = await trigger("organization_name");
    if (valid) setStep(2);
  };

  const handleNextStep2 = async (e: React.FormEvent) => {
    e.preventDefault();
    setServerError('');
    const valid = await trigger(["name", "email", "password", "password_confirmation"]);
    if (valid) setStep(3);
  };

  const onSubmit = async (data: SetupFormData) => {
    setServerError('');
    setIsSubmitting(true);
    try {
      await setupFirstRun(
        data.organization_name.trim(),
        data.name.trim(),
        data.email.trim(),
        data.password,
        data.password_confirmation,
      );
      // Success — session cookie is set by BE, redirect to dashboard
      setStep(4);
      // User clicks the "Mở Dashboard" button below to navigate (no auto-redirect — avoids race with E2E test)
    } catch (err: unknown) {
      if (err instanceof AuthApiError) {
        // Map BE field-level errors to form fields
        if (Object.keys(err.fields).length > 0) {
          for (const [field, msg] of Object.entries(err.fields)) {
            setError(field as keyof SetupFormData, { message: msg });
          }
          setServerError(t('checkInfo'));
        } else {
          const msg = err.message;
          if (msg.includes('AUTH_SETUP_ALREADY_COMPLETED') || msg.includes('setup already')) {
            setServerError(t('alreadyCompleted'));
            setTimeout(() => router.replace('/login'), 1500);
          } else {
            setServerError(getErrorMessage(err.code ?? '') || msg);
          }
        }
      } else if (err instanceof Error) {
        const msg = err.message;
        if (msg.includes('AUTH_SETUP_ALREADY_COMPLETED') || msg.includes('setup already')) {
          setServerError(t('alreadyCompleted'));
          setTimeout(() => router.replace('/login'), 1500);
        } else {
          setServerError(msg);
        }
      } else {
        setServerError(t('error', { error: 'unknown' }));
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  if (backendStatus === 'loading') {
    return (
      <div className="flex items-center justify-center min-h-screen bg-slate-50">
        <div className="animate-spin w-8 h-8 border-2 border-indigo-600 border-t-transparent rounded-full" />
      </div>
    );
  }

  if (backendStatus === 'unavailable') {
    return (
      <div id="setup-unavailable-container" className="flex flex-col items-center justify-center min-h-screen p-6 bg-slate-50 text-slate-900">
        <div className="w-full max-w-md p-8 text-center bg-white rounded-2xl border border-rose-200 shadow-xl shadow-rose-50/50">
          <AlertTriangle className="w-16 h-16 mx-auto mb-4 text-rose-500 animate-pulse" />
          <h1 className="text-2xl font-semibold mb-2">{t('backendUnavailable')}</h1>
          <p className="text-slate-500 mb-6 text-sm leading-relaxed">
            {t('checkBackend')}
          </p>
          <button
            id="retry-connection-button"
            onClick={() => {
              setBackendStatus('loading');
              getSetupStatus()
                .then((status) => {
                  if (status.setup_complete) router.replace('/login');
                  else setBackendStatus('available');
                })
                .catch(() => setBackendStatus('unavailable'));
            }}
            className="w-full py-3 text-sm font-semibold text-slate-700 bg-slate-100 hover:bg-slate-200 rounded-xl transition-all"
          >
            {t('retry')}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div id="setup-wizard-container" className="flex items-center justify-center min-h-screen p-4 bg-slate-50 text-slate-900 relative overflow-hidden">
      {/* Abstract Tech Deco Background */}
      <div className="absolute top-[-20%] left-[-20%] w-[60%] h-[60%] rounded-full bg-indigo-50/40 blur-[120px] pointer-events-none" />
      <div className="absolute bottom-[-20%] right-[-20%] w-[60%] h-[60%] rounded-full bg-indigo-100/30 blur-[120px] pointer-events-none" />

      <div className="w-full max-w-xl p-8 bg-white/95 backdrop-blur-xl rounded-2xl border border-slate-200/80 shadow-xl shadow-slate-100 relative z-10 transition-all duration-300">
        
        {/* Brand Banner */}
        <div className="flex items-center gap-3 mb-8">
          <div className="p-2 bg-gradient-to-tr from-indigo-600 to-indigo-500 rounded-lg text-white font-black tracking-tighter text-xl shadow-md shadow-indigo-100">
            VR
          </div>
          <div>
            <span className="font-sans font-bold text-lg tracking-tight text-slate-900 block">Vroom HR</span>
            <span className="text-xs text-slate-500 block">{t('step', { current: step, total: 4 })}</span>
          </div>
        </div>

        {/* Steps Progress Indicator */}
        {step < 4 && (
          <div className="flex items-center justify-between mb-8">
            {[1, 2, 3].map((s) => (
              <div key={s} className="flex items-center flex-1 last:flex-none">
                <div className={`flex items-center justify-center w-8 h-8 rounded-full text-xs font-mono border transition-all ${
                  step === s 
                    ? 'bg-indigo-600 border-indigo-600 text-white font-bold shadow-md shadow-indigo-100'
                    : step > s 
                      ? 'bg-indigo-50 border-indigo-100 text-indigo-600'
                      : 'bg-transparent border-slate-200 text-slate-400'
                }`}>
                  {s}
                </div>
                {s < 3 && (
                  <div className={`h-[2px] flex-1 mx-3 rounded transition-all ${
                    step > s ? 'bg-indigo-200' : 'bg-slate-200'
                  }`} />
                )}
              </div>
            ))}
          </div>
        )}

        {serverError && (
          <div id="setup-error-msg" className="p-3 mb-6 bg-rose-50 border border-rose-200 text-rose-600 rounded-xl text-sm flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
            <span>{serverError}</span>
          </div>
        )}

        {/* Step 1: Organization Config */}
        {step === 1 && (
          <form id="setup-step1-form" onSubmit={handleNextStep1} className="space-y-6">
            <div>
              <div className="flex items-center gap-2 mb-2 text-indigo-600">
                <Building className="w-5 h-5" />
                <h2 className="text-lg font-bold text-slate-900">{t('step1Title')}</h2>
              </div>
              <p className="text-slate-500 text-sm leading-relaxed">
                {t('step1Desc')}
              </p>
            </div>

            <div>
              <label className="block text-xs font-mono uppercase text-slate-500 mb-2 font-semibold">{t('companyName')}</label>
              <input
                id="setup-org-name-input"
                type="text"
                {...register("organization_name")}
                placeholder={t('companyNamePlaceholder')}
                className="w-full p-3 bg-slate-50 border border-slate-200 rounded-xl text-slate-800 placeholder-slate-400 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
              />
              {errors.organization_name && (
                <p className="mt-1.5 text-xs text-rose-500">{errors.organization_name.message}</p>
              )}
            </div>

            <div className="pt-2">
              <button
                id="setup-step1-submit"
                type="submit"
                className="w-full py-3.5 bg-indigo-600 hover:bg-indigo-700 text-white font-semibold rounded-xl flex items-center justify-center gap-2 shadow-lg shadow-indigo-100 transition-all"
              >
                {t('next')}
                <ArrowRight className="w-4 h-4" />
              </button>
            </div>
          </form>
        )}

        {/* Step 2: HR Account Config */}
        {step === 2 && (
          <form id="setup-step2-form" onSubmit={handleNextStep2} className="space-y-4">
            <div>
              <div className="flex items-center gap-2 mb-2 text-indigo-600">
                <User className="w-5 h-5" />
                <h2 className="text-lg font-bold text-slate-900">{t('step2Title')}</h2>
              </div>
              <p className="text-slate-500 text-sm leading-relaxed">
                {t('step2Desc')}
              </p>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-mono uppercase text-slate-500 mb-1.5 font-semibold">{t('adminName')}</label>
                <input
                  id="setup-hr-name-input"
                  type="text"
                  {...register("name")}
                  placeholder={t('adminNamePlaceholder')}
                  className="w-full p-3 bg-slate-50 border border-slate-200 rounded-xl text-slate-800 placeholder-slate-400 focus:outline-none focus:border-indigo-500"
                />
                {errors.name && (
                  <p className="mt-1.5 text-xs text-rose-500">{errors.name.message}</p>
                )}
              </div>
              <div>
                <label className="block text-xs font-mono uppercase text-slate-500 mb-1.5 font-semibold">{t('adminEmail')}</label>
                <input
                  id="setup-hr-email-input"
                  type="email"
                  {...register("email")}
                  placeholder={t('adminEmailPlaceholder')}
                  className="w-full p-3 bg-slate-50 border border-slate-200 rounded-xl text-slate-800 placeholder-slate-400 focus:outline-none focus:border-indigo-500"
                />
                {errors.email && (
                  <p className="mt-1.5 text-xs text-rose-500">{errors.email.message}</p>
                )}
              </div>
            </div>

            <div>
              <label className="block text-xs font-mono uppercase text-slate-500 mb-1.5 font-semibold">Mật khẩu (≥12 ký tự)</label>
              <input
                id="setup-password-input"
                type="password"
                {...register("password")}
                placeholder="Nhập mật khẩu an toàn..."
                className="w-full p-3 bg-slate-50 border border-slate-200 rounded-xl text-slate-800 placeholder-slate-400 focus:outline-none focus:border-indigo-500"
              />
              {errors.password && (
                <p className="mt-1.5 text-xs text-rose-500">{errors.password.message}</p>
              )}
            </div>

            <div>
              <label className="block text-xs font-mono uppercase text-slate-500 mb-1.5 font-semibold">{t('adminPasswordConfirm')}</label>
              <input
                id="setup-password-confirm-input"
                type="password"
                {...register("password_confirmation")}
                placeholder="Gõ lại mật khẩu..."
                className="w-full p-3 bg-slate-50 border border-slate-200 rounded-xl text-slate-800 placeholder-slate-400 focus:outline-none focus:border-indigo-500"
              />
              {errors.password_confirmation && (
                <p className="mt-1.5 text-xs text-rose-500">{errors.password_confirmation.message}</p>
              )}
            </div>

            <div className="flex items-center gap-3 pt-3">
              <button
                type="button"
                onClick={() => setStep(1)}
                className="flex-1 py-3 bg-slate-100 hover:bg-slate-200 text-slate-600 font-medium rounded-xl transition-all"
              >
                {t('previous')}
              </button>
              <button
                id="setup-step2-submit"
                type="submit"
                className="flex-1 py-3 bg-indigo-600 hover:bg-indigo-700 text-white font-bold rounded-xl flex items-center justify-center gap-2 shadow-lg shadow-indigo-100 transition-all"
              >
                Xem lại thiết lập
              </button>
            </div>
          </form>
        )}

        {/* Step 3: Review Page */}
        {step === 3 && (() => {
          const values = getValues();
          return (
          <form onSubmit={handleSubmit(onSubmit)} className="space-y-6">
            <div>
              <div className="flex items-center gap-2 mb-2 text-indigo-600">
                <Shield className="w-5 h-5" />
                <h2 className="text-lg font-bold text-slate-900">Bước 3: Xác nhận khởi tạo hệ thống</h2>
              </div>
              <p className="text-slate-500 text-sm leading-relaxed">
                Tổ chức và tài khoản HR đầu tiên sẽ được khởi tạo trong một transaction duy nhất. Vui lòng xác nhận thông tin:
              </p>
            </div>

            <div className="p-4 bg-slate-50 rounded-xl border border-slate-200 space-y-3 font-mono text-sm">
              <div className="flex justify-between py-1 border-b border-slate-200">
                <span className="text-slate-500">TỔ CHỨC:</span>
                <span className="text-indigo-600 font-bold">{values.organization_name}</span>
              </div>
              <div className="flex justify-between py-1 border-b border-slate-200">
                <span className="text-slate-500">HỌ TÊN HR:</span>
                <span className="text-slate-800">{values.name}</span>
              </div>
              <div className="flex justify-between py-1 border-b border-slate-200">
                <span className="text-slate-500">EMAIL QUẢN TRỊ:</span>
                <span className="text-slate-800">{values.email}</span>
              </div>
              <div className="flex justify-between py-1">
                <span className="text-slate-500">TIMEZONE MẶC ĐỊNH:</span>
                <span className="text-slate-500">Asia/Ho_Chi_Minh (GMT+7)</span>
              </div>
            </div>

            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={() => setStep(2)}
                className="flex-1 py-3 bg-slate-100 hover:bg-slate-200 text-slate-600 font-medium rounded-xl transition-all"
              >
                {t('previous')}
              </button>
              <button
                id="setup-submit-button"
                type="submit"
                disabled={isSubmitting}
                className="flex-1 py-3 bg-indigo-600 hover:bg-indigo-700 text-white font-bold rounded-xl flex items-center justify-center gap-2 shadow-lg shadow-indigo-100 transition-all disabled:opacity-50"
              >
                {isSubmitting ? 'Đang khởi tạo...' : 'Kích hoạt hệ thống'}
              </button>
            </div>
          </form>
          );
        })()}

        {/* Step 4: Success Screen */}
        {step === 4 && (() => {
          const values = getValues();
          return (
          <div className="text-center py-6 space-y-6">
            <CheckCircle className="w-20 h-20 mx-auto text-indigo-600 animate-bounce" />
            <div>
              <h2 className="text-2xl font-bold text-slate-900 mb-2">Khởi tạo Vroom HR thành công!</h2>
              <p className="text-slate-500 text-sm max-w-md mx-auto leading-relaxed">
                Tổ chức <span className="text-indigo-600 font-semibold">{values.organization_name}</span> đã được thiết lập. Hệ thống đã tự động cấp phiên đăng nhập cho bạn.
              </p>
            </div>

            <div className="p-3 bg-indigo-50 border border-indigo-100 text-indigo-700 rounded-xl text-xs max-w-sm mx-auto font-semibold">
              Trạng thái: Setup complete. Tài khoản System Admin đã sẵn sàng —
              hãy tạo tài khoản HR trong mục Người dùng &amp; Vai trò.
            </div>

            {/* First-run setup mints a System Admin, not an HR account. */}
            <button
              id="setup-open-dashboard-btn"
              onClick={() => { window.location.href = homePathForRole('system_admin'); }}
              className="px-8 py-3.5 bg-gradient-to-r from-indigo-600 to-indigo-500 text-white font-bold rounded-xl hover:from-indigo-500 hover:to-indigo-400 shadow-lg shadow-indigo-200 transition-all"
            >
              Mở Bảng điều khiển hệ thống
            </button>
          </div>
          );
        })()}

      </div>
    </div>
  );
}
