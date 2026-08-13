'use client';
import { useTranslations } from 'next-intl';
import { useSession } from '@/lib/auth/session';
import { User, Clock, FileText, FileSpreadsheet, FolderOpen, Sparkles } from 'lucide-react';
import { useRouter } from '@/i18n/navigation';

export default function EmployeeDashboardPage() {
  const { user } = useSession();
  const router = useRouter();
  const t = useTranslations('employee');
  
      const cards = [
    { href: '/employee/profile', icon: User, title: t('myProfile'), desc: t('myProfileDesc') },
    { href: '/employee/documents', icon: FolderOpen, title: t('myDocuments'), desc: t('myDocumentsDesc') },
    { href: '/employee/attendance', icon: Clock, title: t('attendance'), desc: t('attendanceDesc') },
    { href: '/employee/requests', icon: FileText, title: t('myRequests'), desc: t('myRequestsDesc') },
    { href: '/employee/payslips', icon: FileSpreadsheet, title: t('myPayslips'), desc: t('myPayslipsDesc') },
    { href: '/employee/assistant', icon: Sparkles, title: t('aiAssistant'), desc: t('aiAssistantDesc') },
  ];

  return (
    <div className="space-y-6">
      <div>
        <div className="flex items-center gap-2 text-indigo-600 mb-1">
          <User className="w-5 h-5" />
          <h1 className="text-xl font-bold text-slate-900">{t('dashboardTitle')}</h1>
        </div>
        <p className="text-sm text-slate-500">
          {t('dashboardGreeting', { name: user?.name ?? t('defaultName') })}
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {cards.map((card) => (
          <button
            key={card.href}
            onClick={() => router.push(card.href)}
            className="p-5 text-left bg-white rounded-2xl border border-slate-200 shadow-sm shadow-slate-100 hover:border-indigo-200 hover:shadow-indigo-100 transition-all group"
          >
            <div className="p-2 bg-indigo-50 rounded-lg w-fit mb-3 group-hover:bg-indigo-100 transition-all">
              <card.icon className="w-5 h-5 text-indigo-600" />
            </div>
            <h3 className="font-bold text-sm text-slate-900 mb-1">{card.title}</h3>
            <p className="text-xs text-slate-500 leading-relaxed">{card.desc}</p>
          </button>
        ))}
      </div>
    </div>
  );
}