# Task3B · LOGIN proof/session version cut

2026-10-21 DECISIÓN técnica Codex con arquitectura Sol delivery_path, fuente código api/auth.py login bump y prueba durable3A2. Refinamiento requerido AL IMPLEMENTAR3B, sin cambiar la aprobación histórica3A2 ni habilitar sesiones ahora. PrincipioIX: debe existir antes del consumidor3B.

UnLOGIN factor válido debe conservar la política existente de sesiónúnica y producir prueba durable vigente. No emitirprueba(T)→bump(T+1)→JWT(T+1), porque prueba quedaría vieja. No bump al emitir challenge: contraseña sola no debe cortar sesiones existentes. No nuevo finalizer que convierta receipt/verifiedBoolean en autoridad.

En verify_and_consume_totp_in_cursor, purposeLOGINderivadodeDB: después deOTP real+INSERTcontadorúnico+CASconsumechallenge(T,M), dentro mismaTX CASjax_users T/M/active→token_versionT+1 (mfa_versionM), resetfailed_attempts/locked_until,last_loginNOWcomoéxitoreal. Revalidar admisiónactivaylocked_until canonicalbajouserlock; challenge no sobrevive denegaciónposterior. InvalidarotrasISSUED/EXHAUSTED desujetoexactoldtokenT; no renombrarconsumido/history. AppendLOGIN PASSWORD_PREAUTH actorNULL/challengeIDproofT+1/M, receiptT+1/M. ErrorCAS/eventorollbacktodo(inclcontador/cap/version/cortes). InvalidOTP/replay/expiry no bump. STEPUPsinbumpT/M.

Challenge.expectedT es precondición; proofT+1 es estado postsuccess. FK/ledger conservaambos, ningunaCHECKexigeigualdad.3BNOsegundobump. TrascommitfirmarJWTaccess/refresh currentreceiptversion yDBoccurred_atparaMFA; cortarconexionesexistentesdespués. Classicnofactorconservasubumpexistente. Ningúntoken/cookie antescommitexitoso.

Tests: invalidLOGINsinbump; validLOGINchallengeT→proof/current/receiptT+1 recargabledurable; JWTsameversion yexactunbump; STEPUPneutral; appendfailuretotalrollback; password/MFA/versionchangebetweenissue/complete denies; raceonewinner; oldLOGIN/STEPUP/ENROLLMENTcapabilitiesinvalidatedonlyaftersuccess; Classicunchanged. El escritor3B incluyeprimitivamfa.py enownership porintegración, noflagclient-body.

Costesierrado: reabrir3Bproof/JWTtransactionintegration; alternativaobsoletapruebaproducetokenssinMFAusable oautoridadinventada. No ampliaciónroles ni evidenciafabricada.
