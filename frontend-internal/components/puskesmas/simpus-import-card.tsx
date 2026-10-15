"use client";

import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { toast } from "sonner";
import {
  SimpusConnectIn,
  SimpusPullIn,
  type SimpusConnectInput,
  type SimpusPullInput,
} from "@/lib/api/types";
import { applyApiErrorToForm } from "@/lib/api/form-errors";
import { asApiError } from "@/lib/api/client";
import { useClearSimpusApi, useSaveSimpusApi } from "@/lib/hooks/use-puskesmas";
import { useStartSimpusImport } from "@/lib/hooks/use-scrape";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const CONNECT_FIELDS = ["api_url", "email", "password"] as const;
const PULL_FIELDS = ["tanggal_dari", "tanggal_sampai", "jenis", "create_new"] as const;

const todayJakarta = () =>
  new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Jakarta" }).format(new Date());

function StatusBadge({ ready }: { ready: boolean | undefined }) {
  if (ready === undefined) return null;
  return (
    <span
      className={`text-xs font-medium px-1.5 py-0.5 rounded-md ${
        ready
          ? "bg-green-100 text-green-700"
          : "bg-[var(--muted)] text-[var(--muted-foreground)]"
      }`}
    >
      {ready ? "Terisi" : "Belum diisi"}
    </span>
  );
}

export function SimpusAccountCard({
  puskesmasId,
  apiUrl,
  tokenSet,
}: {
  puskesmasId: string;
  apiUrl: string | null | undefined;
  tokenSet: boolean | undefined;
}) {
  const save = useSaveSimpusApi();
  const clear = useClearSimpusApi();
  const form = useForm<SimpusConnectInput>({
    resolver: zodResolver(SimpusConnectIn),
    defaultValues: { api_url: "", email: "", password: "" },
  });

  useEffect(() => {
    form.setValue("api_url", apiUrl ?? "");
  }, [apiUrl, form]);

  const onSave = form.handleSubmit(async (values) => {
    const email = values.email.trim();
    const password = values.password;
    if ((!email || !password) && !tokenSet) {
      if (!email) form.setError("email", { message: "Email wajib diisi" });
      if (!password) form.setError("password", { message: "Password wajib diisi" });
      return;
    }
    if (Boolean(email) !== Boolean(password)) {
      form.setError("password", { message: "Email dan password diisi bersamaan" });
      return;
    }
    try {
      await save.mutateAsync({
        id: puskesmasId,
        input: { api_url: values.api_url.trim(), email, password },
      });
      form.setValue("email", "");
      form.setValue("password", "");
      toast.success("Akun SIMPUS disimpan");
    } catch (err) {
      applyApiErrorToForm(err, form.setError, CONNECT_FIELDS);
    }
  });

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <CardTitle className="text-base">Akun SIMPUS</CardTitle>
          <StatusBadge ready={tokenSet} />
        </div>
        <CardDescription>
          Akun instansi untuk menarik jawaban CKG. Isi sekali, lalu pakai jadwal
          harian atau tarikan sekali di bawah.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="simpus-url">Alamat SIMPUS</Label>
          <Input
            id="simpus-url"
            placeholder="https://ckg.instansi.go.id"
            {...form.register("api_url")}
          />
          {form.formState.errors.api_url && (
            <p className="text-xs text-red-600">{form.formState.errors.api_url.message}</p>
          )}
        </div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="simpus-email">Email</Label>
            <Input
              id="simpus-email"
              type="email"
              autoComplete="off"
              placeholder={tokenSet ? "Kosongkan jika tidak diganti" : "akun@instansi.id"}
              {...form.register("email")}
            />
            {form.formState.errors.email && (
              <p className="text-xs text-red-600">{form.formState.errors.email.message}</p>
            )}
          </div>
          <div className="space-y-2">
            <Label htmlFor="simpus-password">Password</Label>
            <Input
              id="simpus-password"
              type="password"
              autoComplete="off"
              placeholder={tokenSet ? "Kosongkan jika tidak diganti" : "Password akun"}
              {...form.register("password")}
            />
            {form.formState.errors.password && (
              <p className="text-xs text-red-600">{form.formState.errors.password.message}</p>
            )}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button type="button" size="sm" onClick={onSave} disabled={save.isPending}>
            Simpan akun
          </Button>
          {tokenSet && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={async () => {
                try {
                  await clear.mutateAsync(puskesmasId);
                  form.reset({ api_url: "", email: "", password: "" });
                  toast.success("Akun SIMPUS dihapus");
                } catch (err) {
                  toast.error(asApiError(err).message);
                }
              }}
            >
              Hapus akun
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

export function SimpusPullCard({
  puskesmasId,
  tokenSet,
}: {
  puskesmasId: string;
  tokenSet: boolean | undefined;
}) {
  const start = useStartSimpusImport(puskesmasId);
  const form = useForm<SimpusPullInput>({
    resolver: zodResolver(SimpusPullIn),
    defaultValues: {
      tanggal_dari: todayJakarta(),
      tanggal_sampai: todayJakarta(),
      jenis: "",
      create_new: false,
    },
  });

  const onPull = form.handleSubmit(async (values) => {
    if (!tokenSet) {
      toast.error("Simpan akun SIMPUS dulu");
      return;
    }
    try {
      const job = await start.mutateAsync({
        jenis: values.jenis || undefined,
        tanggal_dari: values.tanggal_dari,
        tanggal_sampai: values.tanggal_sampai,
        create_new: values.create_new,
      });
      toast.success(`Tarikan dimulai (${job.id.slice(0, 8)})`);
    } catch (err) {
      applyApiErrorToForm(err, form.setError, PULL_FIELDS);
    }
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Tarik jawaban sekali</CardTitle>
        <CardDescription>
          Untuk rentang tanggal yang dipilih sekarang. Tidak mengubah jadwal harian.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <div className="space-y-2">
            <Label htmlFor="simpus-dari">Dari tanggal</Label>
            <Input id="simpus-dari" type="date" {...form.register("tanggal_dari")} />
            {form.formState.errors.tanggal_dari && (
              <p className="text-xs text-red-600">
                {form.formState.errors.tanggal_dari.message}
              </p>
            )}
          </div>
          <div className="space-y-2">
            <Label htmlFor="simpus-sampai">Sampai tanggal</Label>
            <Input id="simpus-sampai" type="date" {...form.register("tanggal_sampai")} />
            {form.formState.errors.tanggal_sampai && (
              <p className="text-xs text-red-600">
                {form.formState.errors.tanggal_sampai.message}
              </p>
            )}
          </div>
          <div className="space-y-2">
            <Label>Kunjungan</Label>
            <Select
              value={form.watch("jenis") || "all"}
              onValueChange={(v) =>
                form.setValue("jenis", v === "all" ? "" : (v as "umum" | "sekolah"), {
                  shouldDirty: true,
                })
              }
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Umum dan sekolah</SelectItem>
                <SelectItem value="umum">Umum saja</SelectItem>
                <SelectItem value="sekolah">Sekolah saja</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <input id="simpus-pull-create-new" type="checkbox" {...form.register("create_new")} />
            <Label htmlFor="simpus-pull-create-new" className="cursor-pointer">
              Sekaligus daftarkan pasien yang belum ada di ASIK
            </Label>
          </div>
          <p className="text-xs text-[var(--muted-foreground)]">
            Hanya untuk tarikan ini. Butuh akun ASIK dan alamat default puskesmas.
          </p>
        </div>
        <Button type="button" size="sm" onClick={onPull} disabled={start.isPending || !tokenSet}>
          Tarik sekarang
        </Button>
        {!tokenSet && (
          <p className="text-xs text-[var(--muted-foreground)]">Simpan akun SIMPUS dulu.</p>
        )}
      </CardContent>
    </Card>
  );
}
