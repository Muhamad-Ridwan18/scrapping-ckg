"use client";

import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { toast } from "sonner";
import { SimpusConnectIn, type SimpusConnectInput } from "@/lib/api/types";
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

const FIELDS = ["api_url", "token"] as const;

export function SimpusImportCard({
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
  const start = useStartSimpusImport(puskesmasId);
  const form = useForm<SimpusConnectInput>({
    resolver: zodResolver(SimpusConnectIn),
    defaultValues: {
      api_url: "",
      token: "",
      tanggal_dari: "",
      tanggal_sampai: "",
      jenis: "",
    },
  });

  useEffect(() => {
    form.setValue("api_url", apiUrl ?? "");
  }, [apiUrl, form]);

  const onSave = form.handleSubmit(async (values) => {
    if (!values.token.trim() && !tokenSet) {
      form.setError("token", { message: "Token wajib diisi" });
      return;
    }
    try {
      await save.mutateAsync({
        id: puskesmasId,
        input: { api_url: values.api_url.trim(), token: values.token.trim() },
      });
      form.setValue("token", "");
      toast.success("Koneksi SIMPUS disimpan");
    } catch (err) {
      applyApiErrorToForm(err, form.setError, FIELDS);
    }
  });

  const onImport = async () => {
    const values = form.getValues();
    try {
      if (values.api_url.trim() && values.token.trim()) {
        await save.mutateAsync({
          id: puskesmasId,
          input: { api_url: values.api_url.trim(), token: values.token.trim() },
        });
        form.setValue("token", "");
      } else if (!tokenSet || !apiUrl) {
        toast.error("Isi URL dan token SIMPUS dulu");
        return;
      }
      const job = await start.mutateAsync({
        jenis: values.jenis || undefined,
        tanggal_dari: values.tanggal_dari || undefined,
        tanggal_sampai: values.tanggal_sampai || undefined,
      });
      toast.success(`Impor dimulai (${job.id.slice(0, 8)})`);
    } catch (err) {
      applyApiErrorToForm(err, form.setError, FIELDS);
    }
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <CardTitle className="text-base">SIMPUS</CardTitle>
          <span
            className={`text-xs font-medium px-1.5 py-0.5 rounded-md ${
              tokenSet
                ? "bg-green-100 text-green-700"
                : "bg-[var(--muted)] text-[var(--muted-foreground)]"
            }`}
          >
            {tokenSet ? "Token tersimpan" : "Belum diisi"}
          </span>
        </div>
        <CardDescription>
          Tarik jawaban CKG dari GET /api/v1/ckg/jawaban. Isi URL publik HTTPS
          (tunnel boleh). Localhost ditolak.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="simpus-url">URL SIMPUS</Label>
          <Input
            id="simpus-url"
            placeholder="https://ckg-dev.example.com"
            {...form.register("api_url")}
          />
          {form.formState.errors.api_url && (
            <p className="text-xs text-red-600">{form.formState.errors.api_url.message}</p>
          )}
        </div>
        <div className="space-y-2">
          <Label htmlFor="simpus-token">Token</Label>
          <Input
            id="simpus-token"
            type="password"
            autoComplete="off"
            placeholder={tokenSet ? "Kosongkan jika tidak diganti" : "Bearer token"}
            {...form.register("token")}
          />
          {form.formState.errors.token && (
            <p className="text-xs text-red-600">{form.formState.errors.token.message}</p>
          )}
        </div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <div className="space-y-2">
            <Label htmlFor="simpus-dari">Tanggal dari</Label>
            <Input id="simpus-dari" type="date" {...form.register("tanggal_dari")} />
          </div>
          <div className="space-y-2">
            <Label htmlFor="simpus-sampai">Tanggal sampai</Label>
            <Input id="simpus-sampai" type="date" {...form.register("tanggal_sampai")} />
          </div>
          <div className="space-y-2">
            <Label>Jenis</Label>
            <Select
              value={form.watch("jenis") || "all"}
              onValueChange={(v) =>
                form.setValue("jenis", v === "all" ? "" : (v as "umum" | "sekolah"))
              }
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Umum dan sekolah</SelectItem>
                <SelectItem value="umum">Umum</SelectItem>
                <SelectItem value="sekolah">Sekolah</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button type="button" variant="outline" onClick={onSave} disabled={save.isPending}>
            Simpan koneksi
          </Button>
          <Button type="button" onClick={onImport} disabled={start.isPending || save.isPending}>
            Tarik jawaban
          </Button>
          {tokenSet && (
            <Button
              type="button"
              variant="ghost"
              onClick={async () => {
                try {
                  await clear.mutateAsync(puskesmasId);
                  form.reset({
                    api_url: "",
                    token: "",
                    tanggal_dari: form.getValues("tanggal_dari"),
                    tanggal_sampai: form.getValues("tanggal_sampai"),
                    jenis: form.getValues("jenis"),
                  });
                  toast.success("Koneksi SIMPUS dihapus");
                } catch (err) {
                  toast.error(asApiError(err).message);
                }
              }}
            >
              Hapus
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
