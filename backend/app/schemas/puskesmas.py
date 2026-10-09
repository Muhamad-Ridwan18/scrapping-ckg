import ipaddress
import re
import uuid
from datetime import datetime
from typing import Annotated
from urllib.parse import urlparse

from pydantic import AfterValidator, BaseModel, ConfigDict, field_validator, model_validator

_BASE_URL_RE = re.compile(
    r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$"
)

# Reserved / internal name spaces that must never be a scrape target — the
# headless browser navigates these URLs server-side, so allowing them is SSRF
# (cloud metadata, intranet hosts, loopback). Real portals live on public TLDs
# (.com / .id / .go.id …), so blocking these costs nothing.
_BLOCKED_HOSTS = frozenset({"localhost", "metadata", "metadata.google.internal"})
_INTERNAL_SUFFIXES = (
    ".local", ".localhost", ".internal", ".intranet", ".lan",
    ".home", ".corp", ".test", ".example", ".invalid",
)


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _validate_base_url(v: str) -> str:
    if not _BASE_URL_RE.match(v):
        raise ValueError(
            "must be a base domain only (e.g. 'domain.com', 'sub.domain.id') "
            "without scheme, path, query, or fragment"
        )
    host = v.lower()
    # Defensive: the regex already requires an alphabetic TLD (so bare IPv4/IPv6
    # can't match), but reject IP literals explicitly in case it's ever relaxed.
    if _is_ip_literal(host):
        raise ValueError("must be a domain name, not an IP address")
    if host in _BLOCKED_HOSTS or host.endswith(_INTERNAL_SUFFIXES):
        raise ValueError("internal/reserved hostnames are not allowed as a scrape target")
    # NOTE: this cannot stop DNS rebinding (a public name resolving to a private
    # IP). The defence-in-depth for that is network egress policy on the scraper
    # container — it must not be able to route to RFC1918 / 169.254.0.0/16.
    return v


BaseUrl = Annotated[str, AfterValidator(_validate_base_url)]


def _validate_simpus_api_url(v: str) -> str:
    """Public http(s) origin or the jawaban endpoint itself.

    The import worker calls this URL from the server, so loopback, private
    IPs, and internal suffixes are rejected the same way as scrape targets.
    A Cloudflare (or similar) hostname in front of a local SIMPUS is allowed.
    """
    parsed = urlparse(v.strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("must be an http(s) URL with a host")
    if parsed.username or parsed.password:
        raise ValueError("credentials in the URL are not allowed")
    if parsed.query or parsed.fragment:
        raise ValueError("query and fragment are not allowed")
    host = parsed.hostname.lower()
    if _is_ip_literal(host):
        ip = ipaddress.ip_address(host)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ValueError("private or reserved IP addresses are not allowed")
    if host in _BLOCKED_HOSTS or host.endswith(_INTERNAL_SUFFIXES):
        raise ValueError("internal/reserved hostnames are not allowed")
    port = f":{parsed.port}" if parsed.port else ""
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{host}{port}{path}"


SimpusApiUrl = Annotated[str, AfterValidator(_validate_simpus_api_url)]


class AsikAlamatLevel(BaseModel):
    name: str
    code: str


class AsikDefaultAlamat(BaseModel):
    """The puskesmas' default domicile for the ASIK create-patient cascade.

    All four levels are required together — a partial address cannot drive the
    ASIK registration picker (each level is a click). name/code come straight
    from ASIK's teritorial-service list so they match the live cascade exactly.
    """
    provinsi: AsikAlamatLevel
    kota: AsikAlamatLevel
    kecamatan: AsikAlamatLevel
    kelurahan: AsikAlamatLevel


class PuskesmasCreate(BaseModel):
    name: str
    epus_url: BaseUrl
    asik_url: BaseUrl
    asik_default_alamat: AsikDefaultAlamat | None = None


class PuskesmasUpdate(BaseModel):
    name: str | None = None
    epus_url: BaseUrl | None = None
    asik_url: BaseUrl | None = None
    asik_default_alamat: AsikDefaultAlamat | None = None

    @model_validator(mode="after")
    def _no_clearing_urls(self) -> "PuskesmasUpdate":
        for f in ("epus_url", "asik_url"):
            if f in self.model_fields_set and getattr(self, f) is None:
                raise ValueError(
                    f"{f} cannot be cleared; must be a valid base domain"
                )
        return self


class CredIn(BaseModel):
    email: str
    password: str


class CredOut(BaseModel):
    email: str
    password: str


class SimpusApiIn(BaseModel):
    """Instansi login. Blank email and password keep a token already stored."""

    api_url: SimpusApiUrl
    email: str = ""
    password: str = ""

    @field_validator("email")
    @classmethod
    def _strip_email(cls, v: str) -> str:
        return v.strip()

    @model_validator(mode="after")
    def _email_and_password_together(self) -> "SimpusApiIn":
        if bool(self.email) != bool(self.password):
            raise ValueError("email dan password akun instansi harus diisi bersamaan")
        return self


class PuskesmasOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    epus_url: str | None
    asik_url: str | None
    asik_default_alamat: AsikDefaultAlamat | None
    created_at: datetime
    updated_at: datetime


class PuskesmasDetailOut(PuskesmasOut):
    is_epus_cred_set: bool
    is_asik_cred_set: bool
    simpus_api_url: str | None = None
    is_simpus_token_set: bool = False
