<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M08 Operator 镜像：构建、离线导入与使用

Operator 镜像运行 Kubernetes 控制器，负责 DGD 校验、生成工作负载、协调扩缩容及服务发现；推理仍由 M02 runtime 镜像中的引擎执行。导入 Operator 镜像不会自动安装驱动、Device Plugin、模型或推理镜像。

本轮沿用 `deploy/operator/Dockerfile`，包含当前工作区的 M08 修改，不使用原官方 Operator 成品代替。同一架构的 Operator 镜像可管理三种设备，不安装 CUDA、MACA 或 CANN。amd64 与 arm64 标签分别用于对应架构的 Operator 节点；附带 values 的节点亲和性会约束它的调度。DGDR 还可能在画像任务中使用这个镜像的辅助程序；混合架构集群若启用 DGDR，需要内部仓库的多架构镜像，不能仅靠一个单架构标签覆盖全部节点。本轮直接部署 DGD，不启用 DGDR。

## 1. 产物与验证边界

交付目录为沐曦主机 `/var/tmp/dynamo-m08-operator-bundle/`。它包含两个架构的 `dynamo-operator-m08-*.tar.gz`、独立 `dynamo-operator-1.5.1.tgz` Chart、两份 values、三平台 DGD 示例、模型 PV/PVC 示例、本说明和 `SHA256SUMS`。详细镜像身份和验证结果见 [镜像构建记录](results/operator-images.json)。大型镜像包保留在 `/var/tmp`，不加入 Git。

另提供单文件 `/var/tmp/dynamo-m08-operator-bundle.tar` 及同名 `.sha256` 校验文件，方便整包搬运。在目标环境执行 `tar -xf dynamo-m08-operator-bundle.tar` 后进入解压目录，再执行下面的导入和安装步骤。整包约 169 MiB；不包含 M02 runtime 镜像、模型、kubectl 或 Helm CLI。

Operator 与 CRD 均来自当前源码；Chart 保持版本 1.5.1，HEYI 镜像使用 `1.5.1-m08-<arch>` 标签。10 月 28 日完成构建及本地验证；10 月 29 日已在 CUDA / Ascend 集群导入并安装，现场修订清单与验收结果见 [集群记录](cluster/README.md)。镜像没有推送公共仓库；原离线包保留原校验和，现场使用修订后的 KV/PD 清单。

两种架构均已实际构建，并在各自原生主机完成 `docker save` → `docker load` 回读。镜像内容检查覆盖三个 ELF 程序、六份 CRD、非 root 用户、许可证和无运行时代理环境；两次许可证校验均为 98 个包、零违规。Docker 不同存储后端可能分别以 OCI index digest 或 config digest 显示 image ID，记录中同时保留这些身份；离线文件校验以包的 SHA256 为准。

## 2. 从源码重新构建

准备 Linux、Docker Engine、Docker Buildx、Python 3、gzip 和可用网络。构建需要访问 Go 模块代理、Debian 软件源、PyPI、Docker Hub 和 NGC；运行最终镜像不需要这些下载源。脚本只构建本机架构，不自动安装 QEMU 或修改 Docker daemon。

```bash
cd /home/metax/CJ_workspace/dynamo
docker buildx version

# x86_64 主机：
bash adapter/m08/build-operator.sh amd64 /var/tmp/dynamo-m08-amd64

# 在具有相同源码的 aarch64 主机上：
bash adapter/m08/build-operator.sh arm64 /var/tmp/dynamo-m08-arm64
```

若 Docker Hub 直连超时，可使用已经核对内容的镜像代理。此次构建使用下面的前缀；NGC distroless 基础镜像仍直接取自原地址：

```bash
DOCKER_PROXY=docker.m.daocloud.io/ \
  bash adapter/m08/build-operator.sh amd64 /var/tmp/dynamo-m08-amd64
```

若 Go 模块下载也超时，需要单独给构建容器配置 HTTP 代理。此次沐曦构建机的代理仅监听本机 `127.0.0.1:7890`，因此使用 host 构建网络：

```bash
DOCKER_PROXY=docker.m.daocloud.io/ \
BUILD_NETWORK=host BUILD_HTTP_PROXY=http://127.0.0.1:7890 \
  bash adapter/m08/build-operator.sh amd64 /var/tmp/dynamo-m08-amd64
```

这里的代理端口是构建机环境配置，换机器时按实际地址设置；不需要代理则省略。镜像代理前缀解决基础镜像拉取，HTTP 代理解决 `RUN` 中的依赖下载，两者用途不同。脚本使用 Docker 预定义代理参数，最终 distroless 镜像不保存该代理环境。此次 ARM 构建通过 SSH 临时回环端口转发复用同一代理，构建连接结束即关闭，没有修改宿主机 Docker daemon 配置。

每次运行都会生成独立 `source.XXXXXX/` 快照，包含三个构建上下文和逐文件 SHA256；复制当前未提交修改，排除 `bin/` 等测试产物。失败后可重试，日志和同名导出包会更新，旧源码快照保留。复用同一个标签时，需要用新包重新导入节点，避免 `Never`/`IfNotPresent` 使用旧镜像。

核心构建命令如下（从仓库根目录执行）：

```bash
docker buildx build --load --platform linux/amd64 --progress plain \
  --build-context compliance=container/compliance \
  --build-context operator-chart=deploy/helm/charts/platform/components/operator \
  -f deploy/operator/Dockerfile \
  -t heyi/dynamo-operator:1.5.1-m08-amd64 deploy/operator
```

`compliance` 是必需的命名上下文，供许可证生成阶段使用；`operator-chart` 供 Dockerfile 的测试阶段使用。[Docker 官方说明](https://docs.docker.com/build/concepts/context/#named-contexts)解释了 `--build-context` 的用法。

构建步骤是：Go 1.26.6 下载锁定的 Go 模块 → 编译 `manager`、`crd-apply`、`dgd-apply-overrides` → 生成 Go SBOM、许可证清单并执行策略校验 → 打包进 `nvcr.io/nvidia/distroless/go:v4.0.9`。最终镜像以 UID/GID `65532:65532` 运行，没有 shell 或编译工具，包含 `/opt/dynamo-operator/crds/` 和 `/legal/`。

脚本随后分别执行三个程序的 `--help`，保存镜像 inspect、构建日志和 metadata，执行 `docker save | gzip` 并生成校验和。原 Dockerfile 最终构建目标没有依赖 `tester`/`linter` 阶段，所以镜像构建成功不表示自动跑过 Go 测试。独立测试步骤为：

```bash
cd deploy/operator
make setup-envtest
go test ./internal/platform ./internal/dra ./internal/dynamo \
  ./internal/webhook/validation ./api/v1alpha1 ./api/v1beta1
```

依赖下载、基础镜像标签和 SBOM 时间戳可能变化，重新构建不保证字节完全一致。此次冻结的源文件清单、基础镜像 digest、最终 image ID 及导出包 SHA256 用于识别实际交付版本。

## 3. 打包 Helm Chart

安装 Helm 3 后，从仓库根目录执行：

```bash
mkdir -p /var/tmp/dynamo-m08-operator-bundle
helm lint deploy/helm/charts/platform/components/operator \
  -f adapter/m08/operator-values-amd64.yaml
helm package deploy/helm/charts/platform/components/operator \
  --destination /var/tmp/dynamo-m08-operator-bundle
cp adapter/m08/operator-values-*.yaml /var/tmp/dynamo-m08-operator-bundle/
```

使用独立 Operator Chart，它没有需要额外下载的子 Chart。本说明的 values 顶层是 `controllerManager`；如果以后改用 platform 总 Chart，需要放进 `dynamo-operator:` 子键，不能直接混用。

## 4. 在目标节点导入镜像

把交付目录复制到目标环境。先校验，再按节点运行时选择一种导入方式：

```bash
cd /path/to/dynamo-m08-operator-bundle
sha256sum -c SHA256SUMS

# amd64 节点；ARM 节点将两处 amd64 换成 arm64。
gzip -dk dynamo-operator-m08-amd64.tar.gz

# containerd 集群，导入 Kubernetes 使用的 k8s.io namespace：
sudo ctr --namespace k8s.io images import dynamo-operator-m08-amd64.tar
sudo crictl images | grep dynamo-operator

# 只有 kubelet 确实使用 Docker/cri-dockerd 时，才使用这一分支：
docker load -i dynamo-operator-m08-amd64.tar
docker image inspect heyi/dynamo-operator:1.5.1-m08-amd64 \
  --format '{{.Id}} {{.Architecture}}'
```

containerd 的 socket 必须是 kubelet 使用的那个；K3s/RKE2 等发行版可能有独立路径和配套命令。`docker load` 不会自动把镜像导入另外一个 containerd。每个允许 Operator 调度的节点都需要导入，或者通过节点亲和性进一步限定到已导入节点。若只有带控制平面 taint 的节点可用，应按集群实际 taint 配置 `controllerManager.tolerations`，而不是删除节点 taint。

这些 values 使用 `pullPolicy: Never`，缺镜像时会明确报 `ErrImageNeverPull`，不会尝试访问公网。若使用内部镜像仓库，先给镜像重新打完整仓库标签并推送，再把 `repository`、`tag` 和 `pullPolicy: IfNotPresent` 一起修改；按需设置 `imagePullSecrets`。本轮没有代你推送任何仓库。

还需要在所有相应推理节点导入 **M02 runtime**：

| 平台 | runtime 镜像 | 引擎版本 |
| --- | --- | --- |
| CUDA | `heyi/dynamo-vllm-cuda-runtime:1.5.1-m02` | vLLM 0.28.0 |
| MetaX | `heyi/dynamo-vllm-metax-runtime:1.5.1-m02` | vLLM-metax 0.26.0 |
| Ascend | `heyi/dynamo-vllm-ascend-runtime:1.5.1-m02` | vLLM-Ascend 0.23.0 |

runtime 包不包含在此次 Operator 包内。从对应构建机器执行 `docker save -o runtime.tar <上表镜像>`，按相同方法导入。不要用 dev 镜像替代验收用 runtime。

## 5. 安装 Operator

目标 Kubernetes 版本须为 **1.30 或以上**（Chart 限制）。管理员应已安装匹配的宿主机驱动、容器运行时和 Device Plugin，并确认节点报告标准整设备资源；详见 [M08 资源契约](README.md#资源契约与来源)。Operator Pod 本身不申请加速卡。

以下以新集群、amd64 Operator 节点为例。使用明确的 kube context：

```bash
CTX=你的集群context
NS=dynamo-system
ARCH=amd64                 # ARM Operator 节点改为 arm64

kubectl --context "$CTX" get nodes -L kubernetes.io/arch
helm --kube-context "$CTX" list -A

# 可先检查完整渲染结果；不会写入集群。
helm template dynamo ./dynamo-operator-1.5.1.tgz \
  --namespace "$NS" --kube-version 1.30.0 \
  -f "operator-values-$ARCH.yaml" > operator-rendered.yaml

helm --kube-context "$CTX" upgrade --install dynamo \
  ./dynamo-operator-1.5.1.tgz \
  --namespace "$NS" --create-namespace \
  -f "operator-values-$ARCH.yaml" --wait --timeout 10m

kubectl --context "$CTX" -n "$NS" rollout status \
  deployment/dynamo-dynamo-operator-controller-manager --timeout=180s
kubectl --context "$CTX" get crd dynamographdeployments.nvidia.com
kubectl --context "$CTX" -n "$NS" logs \
  deployment/dynamo-dynamo-operator-controller-manager -c manager --tail=100
```

`crd-apply` init container 从镜像安装/更新配套 CRD；manager 启动后管理 webhook 证书和 CA。默认不需要另装 cert-manager。提供的配置启用 Kubernetes 服务发现，关闭 Grove、KAI、NATS、etcd、监控 CR 的安装依赖，适合本轮 component DGD 基础回归。

如果集群已经存在 Dynamo Operator，沿用既有 Helm release 及 namespace 升级，先导出 `helm get values ... -a` 和现有 DGD/CRD 作备份。不要直接照新安装示例再启动第二套全局 Operator，也不要删除 CRD 来绕过升级问题。原安装如果属于 platform 总 Chart，应使用其原 Chart 和原 values，通过 `dynamo-operator.controllerManager.manager.image` 覆盖镜像。

## 6. 准备模型并部署推理

需要同 namespace 中已 Bound 的 `models` PVC，挂载根下应存在 `Qwen_Series/Qwen3-0.6B`。三台开发机的 `/NFS_62` 宿主机挂载不会自动出现在新集群中。交付包附带 `models-nfs.yaml`，以 `192.168.10.62:/data/HY/NFS` 为 ReadOnlyMany 示例；应用前修改 NFS 地址/路径和 namespace，确认所有目标节点有 NFS 客户端并能访问服务器。已有模型 PVC 时不应用这个示例。

```bash
kubectl --context "$CTX" apply -f models-nfs.yaml
kubectl --context "$CTX" -n "$NS" get pvc models
```

交付包中 `manifests/` 已包含三平台聚合和 2P3D 清单，默认 namespace 为 `dynamo-system`、PVC 为 `models`、model 为 `/models/Qwen_Series/Qwen3-0.6B`、served model name 为 `m08-model`。按环境编辑副本，先做聚合回归：

```bash
PLATFORM=metax            # 或 cuda / ascend
MANIFEST="manifests/$PLATFORM-aggregate.json"
python3 check-cluster.py --context "$CTX" --manifest "$MANIFEST"
kubectl --context "$CTX" apply --dry-run=server -f "$MANIFEST"
kubectl --context "$CTX" apply -f "$MANIFEST"
kubectl --context "$CTX" -n "$NS" get dgd,pods,svc -o wide
```

在源码工作区可重新生成，包括设置 `--runtime-class`、`--image`、`--model`、`--namespace` 等；不要假设不同集群的 RuntimeClass 名称相同：

```bash
python3 adapter/m08/render.py --platform metax --model-pvc models \
  --name m08-metax --namespace dynamo-system > /tmp/m08-metax.json
```

确认本 DGD 的 Frontend 和两个 Worker Ready 后，查出 Frontend Pod 名称进行端口转发：

```bash
kubectl --context "$CTX" -n "$NS" get pods \
  -l nvidia.com/dynamo-component-type=frontend
# 将下面占位符替换为本次测试 DGD 的 Frontend Pod 名称。
kubectl --context "$CTX" -n "$NS" port-forward pod/FRONTEND_POD 8000:8000
```

另一个终端执行：

```bash
curl -fsS http://127.0.0.1:8000/v1/models
curl -fsS http://127.0.0.1:8000/v1/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"m08-model","prompt":"Explain why the sky is blue.","max_tokens":32,"temperature":0}'
```

继续完成 [目标集群验收清单](README.md#目标集群验收清单)：KV 路由、扩缩容、Worker 重建，再清理聚合 DGD、测试同节点 2P3D。2P3D TP1 需要同节点至少五个整卡分配单位，不能与聚合示例同时占用相同卡数。CUDA / Ascend 的同节点跨 Pod PD 和跨节点聚合已于 10 月 29 日实测；跨节点 PD 不能由聚合测试结果推导，详见 [认证范围](cluster/README.md)。

## 7. 排障与清理

- `ErrImageNeverPull`：检查当前 Pod 所在节点、运行时 socket、`k8s.io` namespace、标签是否一致。`exec format error` 通常表示架构错误。
- init container 未完成：查看 `kubectl logs <operator-pod> -c crd-apply`，检查 CRD/RBAC 权限。
- webhook 连接或证书错误：查看 manager 日志、Secret `webhook-server-cert`、webhook Service/EndpointSlice 和集群网络；保持 `failurePolicy: Fail`，修复就绪后再部署。
- Worker Pending：`kubectl describe pod` 查看资源不足、PVC、亲和性、taint 和 RuntimeClass；CPU Frontend 不应消耗 GPU/NPU。
- Worker CrashLoop：查看 `main` 容器日志，核对厂商 runtime、驱动挂载、模型路径及设备插件注入，不用修改宿主机卡号变量代替插件分配。

只清理本轮 DGD：

```bash
kubectl --context "$CTX" delete -f "$MANIFEST"
```

若要卸载这次新建的 Operator，应先删除本轮 DGD 并等待其工作负载回收，再执行 `helm --kube-context "$CTX" uninstall dynamo -n "$NS"`。CRD、PVC 和模型数据不作为日常清理对象；不要在仍有其他 Dynamo 工作负载的集群卸载共用 Operator。

Helm 的实际安装/查看命令、Profiler/DGDR、DRA 和 SLA 的关系见 [使用说明](helm-profiler-dra.md)。
