{{- define "opensandbox.controller.resources" -}}
requests:
  cpu: {{ .Values.controller.resources.requests.cpu | default "100m" | quote }}
  memory: {{ .Values.controller.resources.requests.memory | default "128Mi" | quote }}
limits:
  cpu: {{ .Values.controller.resources.limits.cpu | default "500m" | quote }}
  memory: {{ .Values.controller.resources.limits.memory | default "512Mi" | quote }}
{{- end -}}

{{- define "opensandbox.server.resources" -}}
requests:
  cpu: {{ .Values.server.resources.requests.cpu | default "100m" | quote }}
  memory: {{ .Values.server.resources.requests.memory | default "128Mi" | quote }}
limits:
  cpu: {{ .Values.server.resources.limits.cpu | default "500m" | quote }}
  memory: {{ .Values.server.resources.limits.memory | default "512Mi" | quote }}
{{- end -}}