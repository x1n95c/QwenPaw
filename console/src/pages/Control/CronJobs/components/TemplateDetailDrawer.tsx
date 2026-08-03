import { useCallback, useEffect, useMemo, useState } from "react";
import { Button, Drawer, Tabs } from "@agentscope-ai/design";
// Spin is not re-exported by the design package; the rest of the console
// pulls it from antd directly.
import { Spin } from "antd";
import { useTranslation } from "react-i18next";
import api from "../../../../api";
import type { CronTemplateInfo } from "../../../../api/types";
import { toTemplateDefinition } from "./packageTemplates";
import {
  templateDescription,
  templateFrequency,
  templateTitle,
} from "./templates";
import styles from "../index.module.less";

interface TemplateDetailDrawerProps {
  open: boolean;
  /** Backend package name; null closes the drawer. */
  packageName: string | null;
  onClose: () => void;
  onUseTemplate?: (info: CronTemplateInfo) => void;
}

/** Files worth previewing: the docs, the payload, batch scripts, skills. */
function previewableFiles(info: CronTemplateInfo): string[] {
  return info.files.filter(
    (path) =>
      path === "TEMPLATE.md" ||
      path === "template.json" ||
      path.startsWith("batch/") ||
      path.endsWith(".md") ||
      path.endsWith(".json") ||
      path.endsWith(".py") ||
      path.endsWith(".sh"),
  );
}

/**
 * Inspect a template package before instantiating it.
 *
 * This exists for review, not decoration: a package can ship shell-running
 * batch scripts and whole skills, so anyone importing one from elsewhere
 * needs to read what is inside before letting a scheduled job run it. The
 * backend scans on import, but a scan is not a substitute for looking.
 */
export function TemplateDetailDrawer({
  open,
  packageName,
  onClose,
  onUseTemplate,
}: TemplateDetailDrawerProps) {
  const { t } = useTranslation();
  const [info, setInfo] = useState<CronTemplateInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const [activeFile, setActiveFile] = useState<string>("");
  const [fileContents, setFileContents] = useState<Record<string, string>>({});
  const [fileLoading, setFileLoading] = useState(false);

  useEffect(() => {
    if (!open || !packageName) {
      setInfo(null);
      setFileContents({});
      setActiveFile("");
      return;
    }
    let cancelled = false;
    setLoading(true);
    api
      .getCronTemplate(packageName)
      .then((data) => {
        if (cancelled) return;
        setInfo(data);
        const files = previewableFiles(data);
        setActiveFile(files[0] || "");
      })
      .catch((error) => {
        console.error("Failed to load cron template detail", error);
        if (!cancelled) setInfo(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, packageName]);

  const loadFile = useCallback(
    async (path: string) => {
      if (!packageName || !path || fileContents[path] !== undefined) return;
      setFileLoading(true);
      try {
        const res = await api.readCronTemplateFile(packageName, path);
        setFileContents((prev) => ({ ...prev, [path]: res.content }));
      } catch (error) {
        console.error("Failed to read template file", error);
        setFileContents((prev) => ({
          ...prev,
          [path]: t("cronJobs.templateFileReadFailed"),
        }));
      } finally {
        setFileLoading(false);
      }
    },
    [packageName, fileContents, t],
  );

  useEffect(() => {
    if (activeFile) loadFile(activeFile);
  }, [activeFile, loadFile]);

  const files = useMemo(() => (info ? previewableFiles(info) : []), [info]);

  // Shipped packages carry i18n keys rather than literal text, so the
  // drawer has to resolve them the same way the picker cards do.
  const display = useMemo(() => {
    if (!info) return null;
    const def = toTemplateDefinition(info);
    return {
      title: templateTitle(def, t),
      description: templateDescription(def, t),
      frequency: templateFrequency(def, t),
    };
  }, [info, t]);

  return (
    <Drawer
      open={open}
      title={
        display
          ? `${info?.emoji ? `${info.emoji} ` : ""}${display.title}`
          : t("cronJobs.templateDetailTitle")
      }
      width={780}
      onClose={onClose}
      footer={
        info && onUseTemplate ? (
          <div style={{ textAlign: "right" }}>
            <Button type="primary" onClick={() => onUseTemplate(info)}>
              {t("cronJobs.useTemplate")}
            </Button>
          </div>
        ) : null
      }
    >
      {loading ? (
        <Spin />
      ) : !info ? (
        <div className={styles.historyEmpty}>
          {t("cronJobs.templateDetailUnavailable")}
        </div>
      ) : (
        <>
          {/* Not clamped here: the drawer is where you come to read it. */}
          <div>{display?.description}</div>
          <div className={styles.templateMeta} style={{ marginTop: 8 }}>
            {[
              display?.frequency,
              info.version_text
                ? t("cronJobs.templateVersion", { version: info.version_text })
                : "",
              info.source === "builtin"
                ? t("cronJobs.templateSourceBuiltin")
                : t("cronJobs.templateSourceUser"),
            ]
              .filter(Boolean)
              .join(" · ")}
          </div>

          {info.tags.length ? (
            <div className={styles.templateTags} style={{ marginTop: 8 }}>
              {info.tags.map((tag) => (
                <span key={tag} className={styles.templateTag}>
                  {tag}
                </span>
              ))}
            </div>
          ) : null}

          <div className={styles.templateMeta} style={{ marginTop: 12 }}>
            {t("cronJobs.templateBatchCount", {
              count: info.batch_files.length,
            })}
            {" · "}
            {t("cronJobs.templateSkillCount", { count: info.skills.length })}
            {info.skills.length ? ` (${info.skills.join(", ")})` : ""}
          </div>

          {/* The resolved batch path is what the agent prompt actually
              receives, so showing it makes a misconfigured template obvious. */}
          {info.batch_entry_path ? (
            <div className={styles.templateMeta} style={{ marginTop: 4 }}>
              {t("cronJobs.templateBatchEntry")}: {info.batch_entry_path}
            </div>
          ) : null}

          <Tabs
            style={{ marginTop: 12 }}
            activeKey={activeFile}
            onChange={setActiveFile}
            items={files.map((path) => ({
              key: path,
              label: path,
              children:
                fileLoading && fileContents[path] === undefined ? (
                  <Spin />
                ) : (
                  <pre className={styles.templateFilePreview}>
                    {path === "TEMPLATE.md"
                      ? info.content || fileContents[path]
                      : fileContents[path]}
                  </pre>
                ),
            }))}
          />
        </>
      )}
    </Drawer>
  );
}
