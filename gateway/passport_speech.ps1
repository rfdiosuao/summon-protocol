param([ValidateSet('stt','tts')][string]$Mode,[string]$InputPath,[string]$OutputPath)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Speech
if ($Mode -eq 'stt') {
    $info=[System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers() | Where-Object { $_.Culture.Name -eq 'zh-CN' } | Select-Object -First 1
    if(-not $info){throw 'Chinese speech recognizer is not installed'}
    $engine=[System.Speech.Recognition.SpeechRecognitionEngine]::new($info)
    try {
        $engine.LoadGrammar([System.Speech.Recognition.DictationGrammar]::new())
        $engine.SetInputToWaveFile($InputPath)
        $result=$engine.Recognize([TimeSpan]::FromSeconds(12))
        $data=@{text='';confidence=0}
        if($result){$data.text=$result.Text;$data.confidence=$result.Confidence}
        [IO.File]::WriteAllText($OutputPath,($data|ConvertTo-Json -Compress),[Text.UTF8Encoding]::new($false))
    } finally {$engine.Dispose()}
} else {
    $synth=[System.Speech.Synthesis.SpeechSynthesizer]::new()
    try {
        $synth.SelectVoiceByHints([System.Speech.Synthesis.VoiceGender]::NotSet,[System.Speech.Synthesis.VoiceAge]::NotSet,0,[Globalization.CultureInfo]::GetCultureInfo('zh-CN'))
        $format=[System.Speech.AudioFormat.SpeechAudioFormatInfo]::new(16000,[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,[System.Speech.AudioFormat.AudioChannel]::Mono)
        $synth.SetOutputToWaveFile($OutputPath,$format)
        $synth.Speak([IO.File]::ReadAllText($InputPath,[Text.Encoding]::UTF8))
    } finally {$synth.Dispose()}
}
