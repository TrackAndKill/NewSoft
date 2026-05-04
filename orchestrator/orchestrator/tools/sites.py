from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import desc, select

from orchestrator.config import settings
from orchestrator.db.models import Approval, Event, Site, SiteContent, SystemState, Task, Venture
from orchestrator.db.session import session_scope
from orchestrator.tools.domains import ToolUnavailable, add_dns_record, list_dns_records

DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,251}\.[a-z]{2,}$", re.I)


def _site_dict(site: Site) -> dict:
    return {c.name: getattr(site, c.name) for c in site.__table__.columns}


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")[:80] or "site"


def ensure_site_for_venture(venture_id: int, domain: str | None = None) -> int:
    with session_scope() as s:
        venture = s.get(Venture, venture_id)
        if venture is None:
            raise ValueError(f"Venture {venture_id} not found")
        existing = s.scalars(select(Site).where(Site.venture_id == venture_id).order_by(desc(Site.created_at))).first()
        if existing:
            if domain and not existing.domain:
                existing.domain = domain.strip().lower()
            return existing.id
        slug = _slug(venture.slug)
        site = Site(venture_id=venture_id, slug=slug, domain=domain.strip().lower() if domain else None, deploy_dir=str(Path(settings.site_base_dir) / slug), status="staging")
        s.add(site); s.flush()
        s.add(Event(kind="site_created", actor="sites", message=f"Site #{site.id} created for venture #{venture_id}", payload={"site_id": site.id, "venture_id": venture_id, "slug": site.slug, "domain": site.domain}))
        return site.id


def _latest_content(s, venture_id: int) -> SiteContent:
    content = s.scalars(select(SiteContent).where(SiteContent.venture_id == venture_id, SiteContent.kind == "landing_page").order_by(desc(SiteContent.created_at))).first()
    if content is None:
        raise ValueError(f"No landing_page SiteContent for venture {venture_id}")
    return content


def stage_site(site_id: int) -> dict:
    with session_scope() as s:
        site = s.get(Site, site_id)
        if site is None:
            raise ValueError(f"Site {site_id} not found")
        content = _latest_content(s, site.venture_id)
        deploy_dir = Path(site.deploy_dir)
        deploy_dir.mkdir(parents=True, exist_ok=True)
        (deploy_dir / "index.html").write_text(content.html, encoding="utf-8")
        site.status = "staging"
        site.last_error = None
        site.updated_at = datetime.now(timezone.utc)
        s.add(Event(kind="site_staged", actor="sites", message=f"Site #{site.id} staged at {site.deploy_dir}", payload={"site_id": site.id, "deploy_dir": site.deploy_dir, "bytes": len(content.html.encode('utf-8'))}))
        return _site_dict(site)


def _dry_run(s, force_live: bool | None = None) -> bool:
    if force_live is True:
        return False
    if force_live is False:
        return True
    state = s.get(SystemState, 1)
    return bool(state.dry_run if state else settings.dry_run)


def request_dns(site_id: int, force_live: bool | None = None) -> dict:
    with session_scope() as s:
        site = s.get(Site, site_id)
        if site is None:
            raise ValueError(f"Site {site_id} not found")
        if not site.domain:
            raise ValueError("site has no domain")
        domain, site_id_val = site.domain, site.id
        dry_run = _dry_run(s, force_live)
        target_ip = settings.vm_ipv4.strip()
        if not target_ip:
            dry_run = True
        if dry_run:
            site.status = "dns_pending"
            site.last_error = "simulated DNS; VM_IPV4 or live mode unavailable" if not target_ip else None
            s.add(Event(kind="site_dns_simulated", actor="sites", message=f"Simulated DNS config for {domain}", payload={"site_id": site.id, "domain": domain, "vm_ipv4": target_ip or None}))
            return _site_dict(site)
    try:
        existing = list_dns_records(domain)
        wanted = [("@", "A"), ("www", "A")]
        changed = []
        for name, typ in wanted:
            has = any(str(r.get("name") or "@").rstrip(".") in {name, domain if name == "@" else f"www.{domain}"} and str(r.get("type", "")).upper() == typ and str(r.get("content")) == target_ip for r in existing)
            if not has:
                changed.append(add_dns_record(domain, name=name, type=typ, content=target_ip, ttl=600))
        with session_scope() as s:
            site = s.get(Site, site_id_val)
            site.status = "dns_pending"; site.last_error = None; site.updated_at = datetime.now(timezone.utc)
            s.add(Event(kind="site_dns_requested", actor="sites", message=f"DNS requested for {domain}", payload={"site_id": site.id, "domain": domain, "changed": len(changed)}))
            return _site_dict(site)
    except ToolUnavailable as e:
        with session_scope() as s:
            site = s.get(Site, site_id_val)
            site.status = "dns_pending"; site.last_error = str(e); site.updated_at = datetime.now(timezone.utc)
            s.add(Event(kind="site_dns_deferred", actor="sites", message=f"DNS deferred for {domain}: {e}", payload={"site_id": site.id, "domain": domain}))
            return _site_dict(site)


def _vhost_http(site: Site) -> str:
    domain = site.domain or f"{site.slug}.invalid"
    return f'''server {{
    listen 80;
    listen [::]:80;
    server_name {domain} www.{domain};
    root {site.deploy_dir};
    index index.html;

    location ^~ /.well-known/acme-challenge/ {{
        root {settings.site_well_known_dir};
        try_files $uri =404;
    }}

    location / {{
        try_files $uri $uri/ /index.html;
    }}
}}
'''


def _vhost_https(site: Site) -> str:
    domain = site.domain or f"{site.slug}.invalid"
    return f'''server {{
    listen 80;
    listen [::]:80;
    server_name {domain} www.{domain};
    location ^~ /.well-known/acme-challenge/ {{ root {settings.site_well_known_dir}; try_files $uri =404; }}
    return 301 https://$host$request_uri;
}}
server {{
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name {domain} www.{domain};
    root {site.deploy_dir};
    index index.html;
    ssl_certificate /etc/letsencrypt/live/{domain}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{domain}/privkey.pem;
    location / {{ try_files $uri $uri/ /index.html; }}
}}
'''


def install_nginx_vhost(site_id: int, force_live: bool | None = None) -> dict:
    with session_scope() as s:
        site = s.get(Site, site_id)
        if site is None:
            raise ValueError(f"Site {site_id} not found")
        Path(settings.nginx_site_dir).mkdir(parents=True, exist_ok=True)
        conf_path = Path(settings.nginx_site_dir) / f"{site.slug}.conf"
        conf_path.write_text(_vhost_http(site), encoding="utf-8")
        dry_run = _dry_run(s, force_live)
        if not dry_run:
            subprocess.run(["sudo", "/usr/local/sbin/newsoft-nginx-reload"], check=True, timeout=30)
        site.status = "nginx_ready" if not dry_run else "nginx_ready (simulated)"
        site.last_error = None
        site.updated_at = datetime.now(timezone.utc)
        s.add(Event(kind="site_nginx_ready", actor="sites", message=f"Nginx vhost written for site #{site.id}", payload={"site_id": site.id, "path": str(conf_path), "dry_run": dry_run}))
        return _site_dict(site)


def issue_cert(site_id: int, force_live: bool | None = None) -> dict:
    with session_scope() as s:
        site = s.get(Site, site_id)
        if site is None:
            raise ValueError(f"Site {site_id} not found")
        dry_run = _dry_run(s, force_live) or not site.domain or not settings.certbot_email
        site.status = "tls_pending"
        s.add(Event(kind="site_tls_pending", actor="sites", message=f"TLS pending for site #{site.id}", payload={"site_id": site.id, "dry_run": dry_run}))
        s.flush()
        if not dry_run:
            subprocess.run(["sudo", "/usr/local/sbin/newsoft-issue-cert", site.domain], check=True, timeout=180)
            conf_path = Path(settings.nginx_site_dir) / f"{site.slug}.conf"
            conf_path.write_text(_vhost_https(site), encoding="utf-8")
            subprocess.run(["sudo", "/usr/local/sbin/newsoft-nginx-reload"], check=True, timeout=30)
        site.status = "live" if not dry_run else "live (simulated)"
        site.deployed_at = datetime.now(timezone.utc)
        site.updated_at = site.deployed_at
        site.last_error = None if not dry_run else "simulated TLS; no certbot call made"
        s.add(Event(kind="site_live", actor="sites", message=f"Site #{site.id} is {site.status}", payload={"site_id": site.id, "domain": site.domain, "dry_run": dry_run}))
        return _site_dict(site)


def teardown_site(site_id: int, force_live: bool | None = None) -> dict:
    with session_scope() as s:
        site = s.get(Site, site_id)
        if site is None:
            raise ValueError(f"Site {site_id} not found")
        conf_path = Path(settings.nginx_site_dir) / f"{site.slug}.conf"
        if conf_path.exists():
            conf_path.unlink()
        if not _dry_run(s, force_live):
            subprocess.run(["sudo", "/usr/local/sbin/newsoft-nginx-reload"], check=True, timeout=30)
        site.status = "failed"
        site.last_error = "torn down"
        site.updated_at = datetime.now(timezone.utc)
        s.add(Event(kind="site_teardown", actor="sites", message=f"Site #{site.id} torn down", payload={"site_id": site.id}))
        return _site_dict(site)


def dns_ready(domain: str) -> bool:
    target = settings.vm_ipv4.strip()
    if not target:
        return False
    try:
        return socket.gethostbyname(domain) == target
    except OSError:
        return False


def site_tick() -> int:
    count = 0
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        sites = s.scalars(select(Site).where(Site.status.in_(["dns_pending", "nginx_ready", "nginx_ready (simulated)"]))).all()
        dry_run = _dry_run(s)
        ids = [(site.id, site.status, site.domain, site.updated_at or site.created_at) for site in sites]
    for site_id, status, domain, updated_at in ids:
        if status == "dns_pending":
            if dry_run or (domain and dns_ready(domain)):
                install_nginx_vhost(site_id)
                count += 1
            elif updated_at and updated_at < now - timedelta(hours=6):
                with session_scope() as s:
                    site = s.get(Site, site_id); site.status = "failed"; site.last_error = "DNS did not propagate"; s.add(Event(kind="site_failed", actor="sites", message=f"Site #{site_id} failed: DNS did not propagate", payload={"site_id": site_id}))
        else:
            issue_cert(site_id)
            count += 1
    return count


def request_site_approval(site_id: int, action: str, requested_by: str = "engineer") -> int:
    if action not in {"configure_dns", "deploy_landing_page", "teardown_site"}:
        raise ValueError(f"unsupported site approval action {action}")
    with session_scope() as s:
        site = s.get(Site, site_id)
        if site is None:
            raise ValueError(f"Site {site_id} not found")
        existing = s.scalars(select(Approval).where(Approval.action == action, Approval.status == "pending")).all()
        for approval in existing:
            if (approval.payload or {}).get("site_id") == site_id:
                return approval.id
        approval = Approval(requested_by=requested_by, action=action, payload={"site_id": site_id, "domain": site.domain, "vm_ipv4": settings.vm_ipv4 or None}, rationale=f"Approve {action} for site #{site_id} ({site.domain or site.slug}).", status="pending")
        s.add(approval); s.flush()
        s.add(Event(kind="approval_requested", actor=requested_by, message=f"{action} approval #{approval.id} for site #{site_id}", payload={"approval_id": approval.id, "site_id": site_id, "domain": site.domain}))
        return approval.id


def execute_site_approval(approval_id: int, force_live: bool | None = None) -> dict:
    with session_scope() as s:
        approval = s.get(Approval, approval_id)
        if approval is None:
            raise ValueError(f"Approval {approval_id} not found")
        if approval.status != "approved":
            raise ValueError(f"Approval {approval_id} is {approval.status}, expected approved")
        action = approval.action
        site_id = int((approval.payload or {}).get("site_id") or 0)
    if action == "configure_dns":
        result = request_dns(site_id, force_live=force_live)
    elif action == "deploy_landing_page":
        result = stage_site(site_id)
        # Keep the state machine moving after deploy approval. If DNS was already
        # requested, staging should not reset the site permanently back to staging.
        with session_scope() as s:
            site = s.get(Site, site_id)
            if site and site.domain:
                site.status = "dns_pending"
                site.updated_at = datetime.now(timezone.utc)
                s.add(Event(kind="site_deploy_approved", actor="sites", message=f"Deploy approved for site #{site_id}; awaiting DNS/site tick.", payload={"site_id": site_id, "domain": site.domain}))
                result = _site_dict(site)
    elif action == "teardown_site":
        result = teardown_site(site_id, force_live=force_live)
    else:
        raise ValueError(f"Unsupported site approval action {action}")
    with session_scope() as s:
        execute_mode = "live" if force_live is True else "simulated" if force_live is False else "system"
        s.add(Event(kind="site_approval_executed", actor="sites", message=f"Site approval #{approval_id} executed: {action}", payload={"approval_id": approval_id, "action": action, "execute_mode": execute_mode}))
        tasks = s.scalars(select(Task).where(Task.approval_id == approval_id)).all()
        for task in tasks:
            task.status = "done" if action in {"configure_dns", "deploy_landing_page"} else "skipped"
            task.completed_at = datetime.now(timezone.utc)
    return result
