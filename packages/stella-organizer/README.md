# Stella organizer 开发源码

本目录是 `@stella/secretary` 内 organizer 子插件的源码单元。构建入口为根目录 `npm run product:pack`，Host 源码打包到 `@stella/secretary/organizer`，客户端所需 schema 与纯函数打包到秘书 Client。当前不单独发布本包，也不承诺 `lib` 独立产物。

开发实现仅覆盖新建本机记录、原生协作和定时的基础试验。原功能的一致性和迁移验收以 `product/feature-parity.md` 为准。
