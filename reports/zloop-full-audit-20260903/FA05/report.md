
# Wave ×´Ì¬»úÉó¼Æ±¨¸æ

Ö´ĞĞ Packet: FA05
¶ÔÏó: E:/zcode/zloop-gen8/src/zloop/wave.py

## 1. ½áÂÛ
Wave ×´Ì¬»úÊµÏÖÂß¼­·ûºÏ VOL-09 ¹æ·¶¡£¹Ø¼ü fence (I6) ºÍ DAG Ğ£ÑéÂß¼­ÔÚ¼ì²éÖĞ£¬Î´·¢ÏÖÖ±½ÓÂß¼­´íÎó¡£

## 2. ×´Ì¬»úÉó¼Æ

### 2.1 ÒÀÀµ¹ØÏµÓë³Ö¾Ã²ßÂÔ
*   alidate_wave (L38-L163) Ö´ĞĞÒÀÀµ acyclicity Ğ£Ñé¡£²ÉÓÃÁË Kahn Ëã·¨ (L129-L149)¡£
*   Ö±½ÓÖ¤¾İ: Kahn Ëã·¨Ó¦ÓÃÔÚ 
odes (existing packets + proposed packets) ÉÏ£¬indeg Âß¼­ÕıÈ· (L130-L135)¡£

### 2.2 I6 Ó²ÆÁÕÏ¼ì²é
*   ccept_result (L180-L198) ¶¨ÒåÁËI6Ó²ÆÁÕÏ£ºstage_revision, packet_revision, launch_id, ÒÔ¼° packet ×´Ì¬±ØĞëÈ«²¿Æ¥Åä  RUNNING¡£
*   Ö±½ÓÖ¤¾İ: ´úÂëÂß¼­ÈçÏÂ:
    `python
    if result.get(stage_revision) != current.get(stage_revision): return (False, stale_stage_revision)
    if result.get(packet_revision) != current.get(packet_revision): return (False, revision_mismatch)
    if result.get(launch_id) != current.get(active_launch_id): return (False, stale_launch)
    if current.get(state) != RUNNING: return (False, not_running)
    `
    È·ÈÏÂú×ãËùÓĞÌõ¼ş¡£

### 2.3 ×´Ì¬»úĞ¹Â¶ÓëÖÕÖ¹ÅĞ¶Ï
*   late_result_guard (L201-L205) ÏÔÊ½ guards  stale results ×÷ÎªÖ¤¾İ¡£
*   supersede_stage_revision (L288-L325) ²»Ö¹ Bumps Revision£¬»¹ÔÚÍ¬Ò»Ô­×Ó²Ù×÷ÖĞ½«¾É revision packet ÉèÎª SUPERSEDED ²¢ÇåÀí ctive_launch_id (L314-L318)¡£´ËÉè¼ÆÓĞĞ§·ÀÖ¹ stale launch µ¼ÖÂµÄ×´Ì¬Ğ¹Â¶¡£

## 3. Î´Öª»òĞè³ÖĞø×·×Ù

*   MockBackend ÊÇÈ·¶¨ĞÔµÄÄ£Äâ£¬¶ÔÓÚ¼¯³ÉºóµÄÉú²ú»·¾³£¬ĞèÈ·ÈÏ ackend ÊµÏÖÊÇ·ñÍêÈ«×ñÊØ VOL-04 Â§9 µÄ WorkerReport ½á¹¹¡£´úÂëÖĞµÄ collect ÒşÊ½ÒÀÀµ´Ë½á¹¹¡£
*   ÎŞÃ÷ÏÔÖØÊÔ/³·ÏúÈ±Ïİ·¢ÏÖ£¬µ«ĞèÒª vents.ndjson ¹Û²âÒÔÈ·ÈÏÔ­×Ó±ä¸üÓĞÎŞ race¡£

