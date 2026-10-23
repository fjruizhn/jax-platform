# MFA e invitaciones Office · contrato previo

2026-10-20 · OP#343/345 · Codex. Sol office_mfa_contract y refinamientos ratificados técnicamente por raíz. Requiere cerrar Task1 y auditar barreras Task2 antes de habilitar aceptación externa o staff nuevo. No acredita implementación.

## Fuentes y reutilización

Reutilizar auth/jwt.py, api/auth.py, auth/middleware.py, crypto_secrets.encrypt_secret/decrypt_db_secret, db.connection/get_pool y db.transaccion. No usar decrypt_secret para TOTP porque tolera texto plano. SMTP existente: smtp_config.cargar_settings/construir_mensaje/enviar, fuera del eventloop. AteneaERP aporta flujo/UX, sin copiar la aceptación de replay o recuperación en arreglo.

Raíz verificó [PyOTP2.10.0 en PyPI](https://pypi.org/project/PyOTP/2.10.0/), [API oficial](https://pyauth.github.io/pyotp/) y [RFC6238](https://datatracker.ietf.org/doc/html/rfc6238). Fijar PyOTP==2.10.0 en requisitos y constraints. El verificador requiere persistencia de uso y límites de intentos; la librería no sustituye esas obligaciones. TOTP estándar6dígitos/30s/SHA1, ventana máxima±1; contador único por usuario/versión previene segundo uso. No generadores QR externos.

## MFA persistida

- jax_users.mfa_version BIGINT default0.
- office_mfa_credential: user PK/FK, state, secret_ciphertext y secreto pendiente separado, pending_enrollment_id/expiración, confirmed_at/timestamps.
- office_mfa_totp_use: PK(user,mfa_version,time_counter), propósito/fecha; INSERT en misma transacción que concede la prueba.
- office_mfa_recovery_code: código individual, user/mfa_version/hash/used_at; aleatorio>=128bits, hashSHA256, consumo atómico. Solo se devuelven una vez; noGETrecoverycodes.
- office_mfa_challenge: hash de capacidad opaca aleatoria, user, propósito, versiones esperadas, intentos, expiración/estado/uso. Pending-login/enrollment/recovery no son accessJWT.
- Eventos de seguridad append-only con actor, finalidad y versiones; nunca OTP/secreto/token/código.

Usuario sin MFA conserva login clásico, pero roles Office privilegiados no satisfacen operaciones hasta enrolar. Con MFA activo, contraseña correcta entrega challenge, sin access ni refresh nuevos. Completar challenge bloquea usuario, versiones y contador; dos challenges de la misma versión tienen un ganador. Reutilizar patrón actual de invalidación de sesiones al concluir login.

Access y refresh transportan mfa_at/mfa_version. Middleware relee la versión; tokens antiguos sin claim equivalen0 y no satisfacen MFA. Refresh conserva mfa_at original. Step-up renueva mfa_at pero conserva iat/exp absolutos del refresh original. reverificar_sesion conserva claims en WS/SSE.

Enrolar/reenrolar, consumir recovery, regenerar, deshabilitar o resetear incrementa token_version Y mfa_version. Después del commit se cortan conexiones WS/SSE con mecanismo existente. Reenrolar invalida pending anterior. Recovery consume código único, revoca sesiones y devuelve solo capacidad opaca RECOVERY_REQUIRED para reenrolar, no sesión privilegiada. Regenerar exige contraseña y MFA reciente. Reset ajeno solo Office root con MFA reciente; no admin general/proyecto/clásico.

La política central no depende de un booleano olvidable: VIEW_PROJECT requiere sesión MFA para roles Office privilegiados y aprobadores; colaboradores ordinarios según grant. MANAGE_PROJECT, PROFESSIONAL_ACTION, ADMINISTER_OFFICE y CREATE_OFFICE_PROJECT requieren recencia siempre. require_recent_mfa solo endurece. Recencia configura OFFICE_MFA_RECENCY_SECONDS validado/acotado; producción exige valor explícito. Pruebas usan300s como configuración de test, no como política comercial inventada.

## Roles y creación de identidades

CREATE_OFFICE_PROJECT solo Office root; general admin delegado administra metadatos/directorio/invitaciones de su tenant, sin contenido implícito. Project admin administra colaboradores/clientes de su proyecto, sin promover admins/global/profesional ni resetear MFA.

Clases canónicas jax_users:

| Clase | Clásico | Office |
|---|---|---|
|INTERNAL|autoridad clásica vigente|staff con membership+grant|
|OFFICE_INTERNAL|solo auth/sesión/MFA/Office|staff con membership+grant|
|EXTERNAL_CLIENT|solo auth/sesión/MFA/Office|COLLABORATOR+CLIENT_PUBLISHED+grupo exacto|

Nuevos colaboradores nacen al aceptar como OFFICE_INTERNAL, rol global Office COLLABORATOR, rol clásico mínimo inerte por barrera, únicamente membership Office y grant COLLABORATOR/INTERNAL del proyecto objetivo. No crear accesoTENANT_SHARED ni membresías clásicas. Existente INTERNAL/OFFICE_INTERNAL activo del mismo tenant se reutiliza; externo incompatible/inactivo/email de otro tenant da conflicto, sin conversión/reactivación. Invitaciones externas crean EXTERNAL_CLIENT colaborador/publicado y relación al grupoCLIENT.

Project admin puede invitar staff nuevo directamente a su propio proyecto mediante servicio confiable; noREQUESTED/aprobación global adicional. Al aceptar se reautoriza invitador activo y grants, y se fijan tenant/namespace/roles/proyecto en servidor. Crear cuenta no concede capacidad profesional ni independencia de aprobación. Membership Office se escribe mediante adaptador transaccional JAX emparejado, junto a aceptación/grant, sin INSERT JXP directo ni commits separados.

## Bootstrap

Comando explícito con target-user-id, actor/principal real y basis-ref; resuelve usuario activo/tenant/clase staff. Nunca email como selector, nombre literal o rol clásico como permiso. El correo preguntado a Fernando sirve para localizar y verificar la cuenta, no para un fallback de autorización.

office_bootstrap_state registra ceremonia inicial permanente: objetivo, actor/fundamento/fecha. Repetición exacta idempotente; otro objetivo o ceremonia ya completada no reinicia. No índice ni límite singleton sobre Office superusers. Después, un root con MFA reciente puede promover otros usuarios en API auditada. Pérdida de todos los roots requiere ceremonia de recuperación explícita, no reactivar primerbootstrap. No altera roles clásicos/Faro.

## Invitación y entrega

office_invitation almacena solo hashSHA256 de token256bits, email normalizado, usuario ligado opcional, tenant/proyecto/grupo/clase/grant fijados por servidor, expiración/estado/actor/aceptación y unicidad de invitación pendiente. No signup público y aceptar no inicia sesión automáticamente.

office_invitation_outbox: invitation/estado/attempt_id/intento/claim/message_id/payloadFernet/resultadoSMTP/timestamps. El enlace crudo solo existe en payload cifrado transitorio hasta terminar o abandonar el envío; se elimina en SMTP_ACCEPTED/UNKNOWN/revocación/expiración. Esta excepción explícita concilia token-hash con entrega durable.

Estados QUEUED→CLAIMED→SMTP_ACCEPTED|UNKNOWN; CANCELLED si ya no procede. Reclamo atómico, revalidación de invitación antes de enviar, SMTP en to_thread. SMTP_ACCEPTED es aceptación del relay, no bandeja/lectura. Fallo después del reclamo o muerte conCLAIMED deja UNKNOWN; no reenvío automático. El operador reconcilia o revoca/gira un token nuevo.

Aceptar exige bearer válido, invitación pendiente/vigente/no usada, identidad compatible y autoridad actual; no exigeSMTP_ACCEPTED. Puede llegar correo con estadoUNKNOWN. Registrar INVITATION_BEARER_PRESENTED y outbox_state_at_acceptance, sin reescribir retrospectivamente el transporte. AceptaciónQUEUED cancela/borracifrado; CLAIMED marca cancel_requested, puede terminar envío y registrar resultado sin revivir invitación. Fencing porattempt_id impide resultados viejos.

Sin cuota comercial inventada; sí límites técnicos configurables de tasa/cuerpo/concurrencia e índices/EXPLAIN reales.

## Superficies y verificación

Auth endpoints MFA login/verify, login/recover, status, enroll/start/confirm, step-up, recovery-codes/regenerate, disable. Office endpoints proyectos/lista/detalle, people/roles/delegations/grants/invitations/accept y root resetMFA. Acceptance token nunca en access logs: preferir body opaco o mecanismo de fragmento frontend que lo entregue alPOST; no path/query sensible visible por proxy. Allowlist Task2 admite únicamente rutas reales explícitas.

Pruebas reales: replayTOTP y challenges concurrentes, expirados/usados, cifrado ausente/corrupto, reenrolamiento invalida pending, recoveryconcurrente unganador, refreshnoextiende recencia/expiración, todaslasversiones cortan HTTP/WS/SSE, bootstrapnoinfiereidentidad ni singleton, adminsnoescalan, OfficeInternalnoentra clásico, dobleaceptación produceunaidentidad/membership/grant, invitaciónrevocada/ajena falla, SMTPsimuladosinred y resultadosambiguos/no reenvío, aceptaciónUNKNOWN/QUEUED/CLAIMED sinmentirrecibos, secretos ausentes de logs/audit/adminresponses. EXPLAIN challengehash/contador/invitationhash/lista/outbox y carga antes de producción.

## Barrera de administración de identidad · refinamiento previo

ArquitecturaSol office_workflow_contract confirmó bypassconcreto: classicSUPERADMINdelmismotenant puede POST/api/admin/users/{id}/password oreset-link, oPUTemail yrecuperación, ytomar un OfficecollaboratorconMFAopcional peseacarecer grantsOffice. No se asume que un administrador clásico sea administradorOffice.

Office-enabled = account_classOFFICE_INTERNAL|EXTERNAL_CLIENT O cualquier office_account_profile existente, inclusoREVOKED. Perfil es bindingpermanente: revocar, no borrar. Alhabilitarporprimeravez enbootstrap/invitación/primergrant crearbindingmismaTX e invalidarreset-tokensclásicospendientes/sesiones. ParaINTERNALreutilizado noMFAprivilegiado, el sujeto establececredencialquecontrole mediantebearer antes dehabilitaraccesoOffice; no conservarpasswordpotencialmenteconocidoporclassicadmin.

Guard transaccionalcentral guard_office_identity_mutation_in_cursor traslockstenant/actor/target, deriveclasificacióndebase, tipomutaciónservidor. Operaciónrootonly ADMINISTER_OFFICE_IDENTITY_SECURITY conMFAreciente, noADMINISTER_OFFICEdelegado. Enrutasclásicas yaSUPERADMINserequierenAMBASautoridadesparatargetOffice: passwordajena/resetlinkadmin/email/rol/reactivar/unlock/baja exigenOfficerootactivo+MFAreciente. Revocarsesiones yactive→inactive se conservan pararespuesta de seguridad clásica. Reset/deshabilitarMFAajeno soloendpointOffice dedicado, nuncaclásico. UsuarioOfficeInternalrootpuedeusarendpointOffice sin necesitarrolclásico; no se le abreAPIclásica porserroot.

Self-service forgot/reset/me-password permanece limitadoalsujeto conpruebacorrespondiente y nunca deshabilitaMFA. Resetpassword conmfaactivo mantienechallengealpróximologin. Tests dosadmins distintos,targetsOfficeInternal/external/INTERNALconperfilinclusorevocado,resetpassword/email-resetlink bypassdenied,classic-onlytargetunchanged,ownserviceallowed,sesionesrevoke/deactivationallowed. Barrera seinstalaantesdecrearcuentas externas.

## Prueba de emisión y aceptación diferida

Emitirinvitación requiere autoridadactual yMFArecienteenmismaTX. Persistir proofversion/issuer/tenantprojectgroupclassgrantintent/basisrowversion/profileversion/mfa_version/mfa_at/eventoMFA/eventoautorización/issuedexpiry/proofdigest exacto. Aceptaciónporbearerverificaprueba/lifetime/eventosemisión; issuerperfilactivo y mfa_versionactual==emitida, autoridadactualexacta. No requerir que mfa_at sigareciente24hdespués, ni token_versionigualtraslogin normal. CambioMFA/recovery/reset/deshabilitar/enrolar invalida invitacionesprevias. Basisactualpuedediferirdelhistórico siactualmenteválido. Nunca fabricar sesióninteractivafresca delissuer.

Ejecuciónaceptación = office-invitation:{UUID}; fundamentoautorización = user:{issued_by}. CoremembershiprecibetypedexecutionINVITATION_ACCEPTANCE y registraCAPABILITYconissuerFKenrequest/eventbinding. LedgerOffice registraautorizaciónactualdelissuer como fundamento yeventoINVITATION_BEARER_PRESENTED identificacapacidad, enlazadosporinvitación/correlación. No pseudousuario y ningún token/OTP/códigocrudo enregistro.

## Durable MFA evidence · refinement before3B

2026-10-20 · DECISIÓN técnica Codex from readonly Sol office_execution_contract_refinement, sourceinspection mfa.py WIP and canonical AUTHORIZATION ledger shape. Not productionstate or enabledAPI.

Use dedicated append-only office_mfa_security_event; do not repurpose office_authorization_event ALLOWED/DENIED authenticated authority semantics. New table: event_id PK; event_kind MFA_PROOF|MFA_STATE_CHANGE|SYSTEM_FENCE; actual actor_user_id nullableFK; principal_kind USER_SESSION|PASSWORD_PREAUTH|RECOVERY_CAPABILITY|ENROLLMENT_CAPABILITY|SYSTEM_MIGRATOR; challenge_id nullableFK to new nonsecret surrogate key on office_mfa_challenge (token_hash remains unique); subject_user_id FK; strict operation/purpose; subject token_version/mfa_version and previous versions for statechange; proof_event_id selfFK; authorization_event_id FK; result VERIFIED|DENIED|RECORDED; servercorrelationUUID/DBoccurredtime; nullableuniquefence_key. USER_SESSION requires actualactor, capabilityprincipals require actorNULL+challengeID. Target isalwayssubject. No raw/hash bearer, OTP, secret, recoverycode, ciphertext,email,IP,body/clientcorrelation inevent. ExplicitCOALESCE/NULL-safe shapechecks and enumcase fidelity.

Independent new MFA SYSTEM_FENCE row: systemmigratorprincipal/fixedfencekey/actorNULL/allsubjectproofversionfieldsNULL; UPDATE/DELETE rejecting triggers; exactmetadata androlledbackbehavioralruntimeprobes. Preserve existingauthorizationfenceunchanged. Both fences required beforewebready; bump schemarevision.

Remove freelyconstructible MFAProof as authorityinput. Successfulchallengeconsume atomically appendsMFA_PROOF and returnsimmutablereceipt event_id,subject,purpose,token_version,mfa_version,DBoccurred_at. Mutations takeeventID and reloadunderactor/subjectlocks sameTX, verify realactor/subject/purpose/result/currentversions/recency/nonfuturetime.3B mustnotmanufactureproofdata orbooleanverified.

Active reenrollment keeps oldfactor untilatomicconfirmation; startrequires current selfproof+password or same-subjectRECOVERYcapability. Confirmchecks pendingcapability/OTP, swapssecret, incrementsbothversions, invalidatespriorpending/recovery, appendsproof+stateevents sameTX. Recovery resolves subject fromcode row, nevercallerIDauthority; consumeonecode, incrementversions, clearfactor, returnonlysame-subjectRECOVERYenrollmentcapability. No access/refresh/stepupproof fromrecovery. Freshchallengecannotresetattemptbudget: onelivechallenge orsubject/version/purposebudget persists untilpolicywindowexpiry. EnrollmentOTPalsoattemptlimited.

Root reset sameTX canonicalactor+targetlocks, durableactorMFAproof, current ADMINISTER_OFFICE_IDENTITY_SECURITY authorization+separateALLOWEDauthorizationevent, targetversion/factorchange, MFAeventrealactor+target+bothproof/authorizationreferences. Rootneverchoosessecretforotheruser.3A suppliesin-cursorstate/primitives/fences/receipt;3B suppliesauth/password/rate/config/JWT/currentOfficeauthorization and transactionorchestration/postcommitconnectioncuts. Guardresetdisableduntilcurrentauthorityadapterexists.

Numerictechnicalpolicy: sourceauth/constantes.pyACCESS900, api/auth.pyMAX_ATTEMPTS5/lockout15min andresetbearer3600. 3B productionparser explicitconfigwithin challenge lifetime1..900/attempts1..5/enrollmentlifetime1..3600/recency1..900.3A structuralpositiveonly; test120/3/300/300/3isnotproductiondefault. Recoverycount no canonicalupperyet: rootmustmeasure/documentresponsesize/loadbackedtechnicalmaximum before3Benabled, notinventcommercialquota.

RequiredrealDB tests: truthfulrootactor/target; fake/missing/stale/wrongpurpose/subject/future/oldversionproofdeny; currentrootrevocationbetweenrequest/TXdeny; eitherappendfailure rollsbackall; oneverifiedeventunderconcurrency; oldfactorworkswhilepending; recoverycannotcrosssubject/minttokens; repeatedchallengecannotresetattempts; malformedSQLshapechecks; bothfencesunderrealDMLprincipal; sentinelsabsentaudits/logs.

### Cohesive proof-minting boundary

Sol+Codex,2026-10-20:3A1 schema/fences ONLY, no production proofwriter withoutverifiercaller.3A2 ownscohesive verify_and_consume_totp_in_cursor: derivechallenge/user/credential/subject/purpose/versions fromlockedrows, verifyOTP, uniqueinsertcounter, CASchallengeCONSUMED, appendMFA_PROOF, returnreceipt sameTX. Invalid/replay/exhausted emitsnoVERIFIEDevent. Enrollment/recovery ownsamecohesiveverification+statefunctions. Privateappendhelpers mayreuseinternalderivedrows, neverpublicactor/subject/result/verifiedboolwriter.3B password/session-authenticatedchallengeissuance/JWT/currentOfficeauthority/rootreset/invitationissuance. No callbackprotocolneededbeforeanyrouteexists. TrustedDML/PythoncanalwaysINSERT; threatmodelisuntrustedrequests/confusedorchestrator, enforcedcallgraph+transaction+tests, notunforgeabilityagainstalltrustedservercode.

### Recovery-count technical bound · measured2026-10-20

DECISIÓN técnica Codex:3B explicitconfiguredrecovery_code_count1..64; no silentdefault. Chooseboundedsecretresponsebudget4KiB andhighestmeasuredcandidatebelowit, notcommercialquota. Root measured generation/hash/executemany+commit+JSON12samples at8/16/32/64/128 in isolated33991 syntheticuser:64codes2260B, median2.852ms/p953.219ms;128codes4500B. Canonicalcodeentropyunchanged192bits. Evidence office-mfa-policy-20261020 rawreceipt+script. ThisisnotfullHTTPconcurrency/load/authproof;3Bmuststillmeasureitsactualendpoints. Ifproductlaterneedsmorethan64, reviseexplicittechnicalresponsebudgetwithnewmeasurement; no authoritylimitbyuser/tenant.3Astructuralvalidationremainspositive;3Bproductionparserenforcesboundandrequiredconfig.
