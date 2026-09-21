{ Altium iBOM one-button launcher. }

function IsSupportedInput(const FileName : String) : Boolean;
var
    Ext : String;
begin
    Ext := LowerCase(ExtractFileExt(FileName));
    Result := (Ext = '.prjpcb') Or (Ext = '.pcbdoc');
end;

function FocusedAltiumInput : String;
var
    Workspace : IWorkspace;
    Project   : IProject;
    Document  : IDocument;
    Board     : IPCB_Board;
begin
    Result := '';
    Workspace := GetWorkspace;

    if Workspace <> Nil then
    begin
        Project := Workspace.DM_FocusedProject;
        if Project <> Nil then
        begin
            Result := Project.DM_ProjectFullPath;
            if Not IsSupportedInput(Result) then
                Result := '';
        end;

        if Result = '' then
        begin
            Document := Workspace.DM_FocusedDocument;
            if Document <> Nil then
            begin
                Result := Document.DM_FullPath;
                if Not IsSupportedInput(Result) then
                    Result := '';
            end;
        end;
    end;

    if Result = '' then
    begin
        Board := PCBServer.GetCurrentPCBBoard;
        if Board <> Nil then
        begin
            Result := Board.FileName;
            if Not IsSupportedInput(Result) then
                Result := '';
        end;
    end;
end;

procedure GenerateInteractiveBom;
var
    SourcePath  : String;
    CommandLine : String;
    ErrorCode   : Integer;
begin
    SourcePath := FocusedAltiumInput;
    if SourcePath = '' then
    begin
        ShowError('Open or focus a PCB project (.PrjPcb) or PCB document (.PcbDoc), then try again.');
        Exit;
    end;

    CommandLine := 'cmd.exe /D /C ""__ALTIUM_IBOM_LAUNCHER__" "' + SourcePath + '""';
    ErrorCode := RunApplication(CommandLine);
    if ErrorCode <> 0 then
        ShowError('Could not start Altium iBOM: ' + GetErrorMessage(ErrorCode));
end;
