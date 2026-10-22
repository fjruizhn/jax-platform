# Inventario de publicación Office

2026-10-21 local · HECHO observado por office_publication_facts (Tier1) y comprobación independiente parcial Codex. Solo lectura; no acredita despliegue ni sustituye verificación al activar.

- Dominio virtualoffice.axioma-ia.io sin A/CNAME, NXDOMAIN. NS vigentes ali.ns.cloudflare.com y keaton.ns.cloudflare.com (corrección explícita de reporte intermedio que citaba dns.cloudflare.com).
- Piloto corporativo-virtual-pilot.service es unidad de USUARIO; `systemctl --user is-active` active y curl127.0.0.1:4173 devuelve200. Preservar servicio/datos; systemctl sin --user consulta otro espacio y no acredita caída.
- Backend canónico Hall9000 jax-platform.service escucha8080, frontendestático unidadpropia5173. Nginx del sistema Hall inactivo y sites-enabled vacío; archivo sites-available no es ingresspúblico.
- Ingress actual es mail.axioma-ia.io (topología atem-ai,172.16.20.11), aaPanelnginx /www/server/panel/vhost/nginx/axioma-ia.io.conf, raíz /www/wwwroot/axioma-ia.io; /api y /ws a172.16.20.5:8080. Axioma/www resuelven38.7.24.147 segúnexploración. El vhost no incluyevirtualoffice; DNSsolono publica.
- Certificado existente /www/server/panel/vhost/cert/axioma-ia.io/fullchain.pem cubre*.axioma-ia.io y axioma-ia.io, vence2027-01-15 segúnexploración; comprobar al activar. Llaveprivada nunca copiar/imprimir en repo.
- HelperDNS real /usr/local/sbin/cf-dns (root0700), token /etc/cf-dns-token (root0600), APICloudflarefija. Ruta documental /home/sysadmin/bin/cf-dns estabaobsoleta. Uso `cf-dns {verificar|zonas|listar <zona>|crear <zona> <tipo> <nombre> <contenido> [proxied] [prio]|borrar <zona> <id>}`. Renovacióncomodines /usr/local/sbin/renovar-comodines.py usa mismocredencial y aaPanelapi.json. No seleyó/imprimiósecretos enreporte.
- JXP/JAX workflowsobservados noautodespliegan; integración es distinta depublicaciónmanual. Runbooks despliegue.md y frontend-estatico-p100.md definenartefacto/releaseinmutable/canary/rollback, leercompletos alactivar.

DECISIÓN pendiente de implementación: publicarOfficeconservaráelpilotoylawebclásica. ElfrontendOffice necesitaentrada/rutadistinta y revisión desuconfig antesdeelegirraíz/vhost; elinventarionoautoriza sobrescribir /www/wwwroot/axioma-ia.io conunbundleOffice. Backend/identidadcanónicos sereutilizan segúnplan. DNS/vhost/cert/serviciosmigraciones solo despuésdeartefactos+auditoría+restauración+cargaygatesvigentes. No pedir confirmacionestriviales porpaso; aplicarautoridaddeFernandoexistente conformeconstitución alhacerlaacciónconcreta.

2026-10-22 · VERDAD OPERACIONAL rootreadonly3308: VERSION12.3.3-MariaDB-ubu2404 (mismo12.3.3delharness), sql_modeSTRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION; charsetutf8mb4/collationutf8mb4_uca1400_ai_ci; sessionSYSTEM/systemCST. STARTTRANSACTIONREADONLY+rollback, credencialesleídas enmemoria noimpresas. Evitaclaimdecompatibilidadporversióndiferente;reverificarantesmigración.
