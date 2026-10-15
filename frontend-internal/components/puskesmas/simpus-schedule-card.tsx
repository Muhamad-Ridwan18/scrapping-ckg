"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Clock, PlayCircle, Pencil, Trash2 } from "lucide-react";
import { format, parseISO } from "date-fns";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ConfirmDialog } from "@/components/common/confirm-dialog";
import { SimpusScheduleFormDialog } from "./simpus-schedule-form-dialog";
import {
  useDeleteSimpusSchedule,
  useRunSimpusScheduleNow,
  useSaveSimpusSchedule,
  useSimpusSchedule,
} from "@/lib/hooks/use-puskesmas";
import { asApiError } from "@/lib/api/client";

function pad(n: number) {
  return n.toString().padStart(2, "0");
}

export function SimpusScheduleCard({ puskesmasId }: { puskesmasId: string }) {
  const cfgQuery = useSimpusSchedule(puskesmasId);
  const save = useSaveSimpusSchedule(puskesmasId);
  const del = useDeleteSimpusSchedule(puskesmasId);
  const runNow = useRunSimpusScheduleNow(puskesmasId);
  const router = useRouter();
  const [editOpen, setEditOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [runNowOpen, setRunNowOpen] = useState(false);

  const cfg = cfgQuery.data;

  const onToggleEnabled = async () => {
    if (!cfg) return;
    try {
      await save.mutateAsync({
        hour: cfg.hour,
        minute: cfg.minute,
        lookback_days: cfg.lookback_days,
        enabled: !cfg.enabled,
        create_new: cfg.create_new,
      });
      toast.success(cfg.enabled ? "Jadwal dinonaktifkan" : "Jadwal diaktifkan");
    } catch (err) {
      toast.error(asApiError(err).message);
    }
  };

  const onRunNow = async () => {
    try {
      const run = await runNow.mutateAsync();
      toast.success("Tarikan harian dimulai");
      setRunNowOpen(false);
      router.push(`/cron-runs/${run.id}`);
    } catch (err) {
      toast.error(asApiError(err).message);
    }
  };

  const onDelete = async () => {
    try {
      await del.mutateAsync();
      toast.success("Jadwal dihapus");
      setDeleteOpen(false);
    } catch (err) {
      toast.error(asApiError(err).message);
    }
  };

  if (cfgQuery.isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Jadwal harian SIMPUS</CardTitle>
          <CardDescription>Memuat…</CardDescription>
        </CardHeader>
      </Card>
    );
  }

  if (!cfg) {
    return (
      <>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Jadwal harian SIMPUS</CardTitle>
            <CardDescription>
              Belum diatur. Setelah diatur, jawaban CKG ditarik sendiri setiap hari.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button variant="outline" size="sm" onClick={() => setEditOpen(true)}>
              <Clock className="h-4 w-4" />
              Atur jadwal
            </Button>
          </CardContent>
        </Card>
        <SimpusScheduleFormDialog
          open={editOpen}
          onOpenChange={setEditOpen}
          puskesmasId={puskesmasId}
          existing={null}
        />
      </>
    );
  }

  return (
    <>
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="text-base">Jadwal harian SIMPUS</CardTitle>
              <CardDescription>
                Setiap hari pukul {pad(cfg.hour)}:{pad(cfg.minute)} ·{" "}
                {cfg.lookback_days === 1
                  ? "hari ini saja"
                  : `${cfg.lookback_days} hari terakhir`}{" "}
                · {cfg.enabled ? "Aktif" : "Nonaktif"}
                {cfg.create_new ? " · Daftarkan pasien baru" : ""}
              </CardDescription>
            </div>
            <Button
              variant={cfg.enabled ? "outline" : "default"}
              size="sm"
              onClick={onToggleEnabled}
              disabled={save.isPending}
            >
              {cfg.enabled ? "Nonaktifkan" : "Aktifkan"}
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid grid-cols-2 gap-2 text-xs text-[var(--muted-foreground)]">
            <div>
              <span className="font-medium">Jalan berikutnya: </span>
              {format(parseISO(cfg.next_run_at), "yyyy-MM-dd HH:mm")}
            </div>
            <div>
              <span className="font-medium">Terakhir dijalankan: </span>
              {cfg.last_fired_at
                ? format(parseISO(cfg.last_fired_at), "yyyy-MM-dd HH:mm")
                : "—"}
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              onClick={() => setRunNowOpen(true)}
              disabled={runNow.isPending}
            >
              <PlayCircle className="h-4 w-4" />
              Jalankan sekarang
            </Button>
            <Button variant="outline" size="sm" onClick={() => setEditOpen(true)}>
              <Pencil className="h-4 w-4" />
              Edit
            </Button>
            <Button variant="outline" size="sm" onClick={() => setDeleteOpen(true)}>
              <Trash2 className="h-4 w-4" />
              Hapus
            </Button>
          </div>
        </CardContent>
      </Card>

      <SimpusScheduleFormDialog
        open={editOpen}
        onOpenChange={setEditOpen}
        puskesmasId={puskesmasId}
        existing={cfg}
      />

      <ConfirmDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        title="Hapus jadwal harian?"
        description="Jawaban tidak lagi ditarik sendiri. Riwayat tarikan yang sudah ada tetap tersimpan."
        confirmLabel="Hapus"
        destructive
        loading={del.isPending}
        onConfirm={onDelete}
      />

      <ConfirmDialog
        open={runNowOpen}
        onOpenChange={setRunNowOpen}
        title="Tarik sesuai jadwal, sekarang?"
        description={`Jawaban ${
          cfg.lookback_days === 1 ? "hari ini" : `${cfg.lookback_days} hari terakhir`
        } akan ditarik sekarang, tanpa menunggu jam jadwal.`}
        confirmLabel="Jalankan sekarang"
        loading={runNow.isPending}
        onConfirm={onRunNow}
      />
    </>
  );
}
