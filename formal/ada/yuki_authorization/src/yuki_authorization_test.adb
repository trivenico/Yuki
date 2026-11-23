with Ada.Assertions;
with Yuki_Authorization;

procedure Yuki_Authorization_Test
  with SPARK_Mode
is
   use Yuki_Authorization;

   Read_Request : Request_Context :=
     (Authenticated           => True,
      Owner_Matches           => True,
      Policy_Found            => True,
      Policy_Enabled          => True,
      Required_Scopes_Granted => True,
      Policy_Revision_Current => True,
      Request_Audit_Recorded  => True,
      Decision_Audit_Recorded => True,
      Risk                    => Read_Only,
      Owner_Approval_Granted  => False);

   Write_Request : Request_Context :=
     (Authenticated           => True,
      Owner_Matches           => True,
      Policy_Found            => True,
      Policy_Enabled          => True,
      Required_Scopes_Granted => True,
      Policy_Revision_Current => True,
      Request_Audit_Recorded  => True,
      Decision_Audit_Recorded => True,
      Risk                    => Write,
      Owner_Approval_Granted  => False);

   Financial_Request : Request_Context :=
     (Authenticated           => True,
      Owner_Matches           => True,
      Policy_Found            => True,
      Policy_Enabled          => True,
      Required_Scopes_Granted => True,
      Policy_Revision_Current => True,
      Request_Audit_Recorded  => True,
      Decision_Audit_Recorded => True,
      Risk                    => Financial,
      Owner_Approval_Granted  => True);

   Denied_Request : Request_Context;
begin
   pragma Assert (May_Execute (Read_Request));
   pragma Assert (not May_Execute (Write_Request));
   pragma Assert (May_Execute (Financial_Request));
   pragma Assert (not May_Execute (Denied_Request));

   Write_Request.Owner_Approval_Granted := True;
   pragma Assert (May_Execute (Write_Request));

   Read_Request.Policy_Revision_Current := False;
   pragma Assert (not May_Execute (Read_Request));

   Financial_Request.Owner_Matches := False;
   pragma Assert (not May_Execute (Financial_Request));

   Financial_Request.Owner_Matches := True;
   Financial_Request.Required_Scopes_Granted := False;
   pragma Assert (not May_Execute (Financial_Request));

   Financial_Request.Required_Scopes_Granted := True;
   Financial_Request.Request_Audit_Recorded := False;
   pragma Assert (not May_Execute (Financial_Request));

   Financial_Request.Request_Audit_Recorded := True;
   Financial_Request.Decision_Audit_Recorded := False;
   pragma Assert (not May_Execute (Financial_Request));

   Ada.Assertions.Assert (May_Execute (Write_Request));
end Yuki_Authorization_Test;
