package Yuki_Authorization
  with SPARK_Mode
is
   type Risk_Class is (Read_Only, Write, Financial);

   --  These values are assertions supplied by the trusted host integration.
   --  They are not populated from model text or remote tool metadata.
   type Request_Context is record
      Authenticated                 : Boolean := False;
      Owner_Matches                 : Boolean := False;
      Policy_Found                  : Boolean := False;
      Policy_Enabled                : Boolean := False;
      Required_Scopes_Granted       : Boolean := False;
      Policy_Revision_Current       : Boolean := False;
      Request_Audit_Recorded        : Boolean := False;
      Decision_Audit_Recorded       : Boolean := False;
      Risk                          : Risk_Class := Write;
      Owner_Approval_Granted        : Boolean := False;
   end record;

   --  Pure decision predicate.  Read-only operations still require exact
   --  owner/policy/scope/revision checks and both audit records.  Write and
   --  financial operations additionally require fresh owner approval.
   function May_Execute (Context : Request_Context) return Boolean
     with
       Global  => null,
       Depends => (May_Execute'Result => Context),
       Post    =>
         May_Execute'Result =
           (Context.Authenticated
            and then Context.Owner_Matches
            and then Context.Policy_Found
            and then Context.Policy_Enabled
            and then Context.Required_Scopes_Granted
            and then Context.Policy_Revision_Current
            and then Context.Request_Audit_Recorded
            and then Context.Decision_Audit_Recorded
            and then
              (Context.Risk = Read_Only
               or else Context.Owner_Approval_Granted));

end Yuki_Authorization;
