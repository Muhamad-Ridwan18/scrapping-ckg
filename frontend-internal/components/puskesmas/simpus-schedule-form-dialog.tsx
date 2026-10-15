"use client";

import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SimpusScheduleIn, type SimpusSchedule, type SimpusScheduleInput } from "@/lib/api/types";
import { useSaveSimpusSchedule } from "@/lib/hooks/use-puskesmas";
import { applyApiErrorToForm } from "@/lib/api/form-errors";

const KNOWN_FIELDS = ["hour", "minute", "lookback_days", "enabled", "create_new"] as const;

const EMPTY: SimpusScheduleInput = {
  hour: 1,
  minute: 0,
  lookback_days: 1,
  enabled: true,
  create_new: false,
};

export function SimpusScheduleFormDialog({
  open,
  onOpenChange,
  puskesmasId,
  existing,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  puskesmasId: string;
  existing: SimpusSchedule | null;
}) {
  const save = useSaveSimpusSchedule(puskesmasId);
  const form = useForm<SimpusScheduleInput>({
    resolver: zodResolver(SimpusScheduleIn),
    defaultValues: EMPTY,
  });

  useEffect(() => {
    if (!open) return;
    form.reset(
      existing
        ? {
            hour: existing.hour,
            minute: existing.minute,
            lookback_days: existing.lookback_days,
            enabled: existing.enabled,
            create_new: existing.create_new,
          }
        : EMPTY,
    );
  }, [open, existing, form]);

  const onSubmit = form.handleSubmit(async (values) => {
    try {
      await save.mutateAsync(values);
      toast.success(existing ? "Jadwal diperbarui" : "Jadwal disimpan");
      onOpenChange(false);
    } catch (err) {
      applyApiErrorToForm(err, form.setError, KNOWN_FIELDS);
    }
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {existing ? "Ubah jadwal harian" : "Atur jadwal harian"}
          </DialogTitle>
          <DialogDescription>
            Jawaban CKG ditarik sendiri setiap hari pada jam Jakarta yang dipilih.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={onSubmit} className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="simpus-cron-hour">Jam</Label>
              <Input
                id="simpus-cron-hour"
                type="number"
                min={0}
                max={23}
                {...form.register("hour", { valueAsNumber: true })}
              />
              {form.formState.errors.hour && (
                <p className="text-xs text-[var(--destructive)]">
                  {form.formState.errors.hour.message}
                </p>
              )}
            </div>
            <div className="space-y-2">
              <Label htmlFor="simpus-cron-minute">Menit</Label>
              <Input
                id="simpus-cron-minute"
                type="number"
                min={0}
                max={59}
                {...form.register("minute", { valueAsNumber: true })}
              />
              {form.formState.errors.minute && (
                <p className="text-xs text-[var(--destructive)]">
                  {form.formState.errors.minute.message}
                </p>
              )}
            </div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="simpus-cron-lookback">Berapa hari ke belakang</Label>
            <Input
              id="simpus-cron-lookback"
              type="number"
              min={1}
              max={14}
              {...form.register("lookback_days", { valueAsNumber: true })}
            />
            {form.formState.errors.lookback_days && (
              <p className="text-xs text-[var(--destructive)]">
                {form.formState.errors.lookback_days.message}
              </p>
            )}
            <p className="text-xs text-[var(--muted-foreground)]">
              1 = hari ini saja. 3 = hari ini dan dua hari sebelumnya. Maksimal 14.
              Waktu jadwal memakai jam Jakarta.
            </p>
          </div>

          <div className="flex items-center gap-2">
            <input id="simpus-cron-enabled" type="checkbox" {...form.register("enabled")} />
            <Label htmlFor="simpus-cron-enabled" className="cursor-pointer">
              Jadwal aktif
            </Label>
          </div>

          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <input
                id="simpus-cron-create-new"
                type="checkbox"
                {...form.register("create_new")}
              />
              <Label htmlFor="simpus-cron-create-new" className="cursor-pointer">
                Sekaligus daftarkan pasien yang belum ada di ASIK
              </Label>
            </div>
            <p className="text-xs text-[var(--muted-foreground)]">
              Hanya untuk jadwal ini. Pasien baru didaftarkan dari nama SIMPUS,
              lalu pemeriksaannya diisi. Butuh akun ASIK dan alamat default.
            </p>
          </div>

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={save.isPending}
            >
              Batal
            </Button>
            <Button type="submit" disabled={save.isPending}>
              {save.isPending ? "Menyimpan…" : existing ? "Simpan" : "Buat"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
