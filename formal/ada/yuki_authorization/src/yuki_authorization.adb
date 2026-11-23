package body Yuki_Authorization
  with SPARK_Mode
is
   function May_Execute (Context : Request_Context) return Boolean is
   begin
      return Context.Authenticated
        and then Context.Owner_Matches
        and then Context.Policy_Found
        and then Context.Policy_Enabled
        and then Context.Required_Scopes_Granted
        and then Context.Policy_Revision_Current
        and then Context.Request_Audit_Recorded
        and then Context.Decision_Audit_Recorded
        and then
          (Context.Risk = Read_Only
           or else Context.Owner_Approval_Granted);
   end May_Execute;
end Yuki_Authorization;
