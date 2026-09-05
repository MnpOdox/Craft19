def migrate(cr, version):
    """Preserve the former single immediate-template configuration as step one."""
    cr.execute("""
        UPDATE odx_meta_form
           SET whatsapp_auto_close_hours = 48
         WHERE whatsapp_auto_send_enabled = TRUE
           AND COALESCE(whatsapp_auto_close_hours, 0) <= 0
    """)
    cr.execute("""
        INSERT INTO odx_meta_whatsapp_followup_step
            (id, sequence, form_id, company_id, account_id, template_id,
             delay_hours, template_parameters, create_uid, write_uid,
             create_date, write_date)
        SELECT nextval('odx_meta_whatsapp_followup_step_id_seq'),
               10, form.id, form.company_id, form.whatsapp_auto_account_id,
               form.whatsapp_auto_template_id, 0,
               form.whatsapp_auto_template_parameters, 1, 1, NOW(), NOW()
          FROM odx_meta_form AS form
         WHERE form.whatsapp_auto_send_enabled = TRUE
           AND form.whatsapp_auto_account_id IS NOT NULL
           AND form.whatsapp_auto_template_id IS NOT NULL
           AND NOT EXISTS (
               SELECT 1
                 FROM odx_meta_whatsapp_followup_step AS step
                WHERE step.form_id = form.id
           )
    """)
