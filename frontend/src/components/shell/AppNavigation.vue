<script setup lang="ts">
import { RouterLink } from 'vue-router'

import { navigationItems, settingsNavigationItem, type NavigationItem } from '../../navigation'

defineProps<{
  collapsed: boolean
}>()

function itemLabel(item: NavigationItem): string {
  return item.label
}
</script>

<template>
  <aside
    id="app-navigation"
    class="app-navigation"
    data-testid="app-navigation"
    :data-collapsed="collapsed ? 'true' : 'false'"
    aria-label="应用导航"
  >
    <nav data-testid="primary-navigation" aria-label="主要导航">
      <ul>
        <li v-for="item in navigationItems" :key="item.id">
          <RouterLink
            v-if="item.availability === 'available' && item.path"
            :to="item.path"
            :aria-label="collapsed ? itemLabel(item) : undefined"
            :title="collapsed ? itemLabel(item) : undefined"
          >
            <span aria-hidden="true">{{ item.symbol }}</span>
            <span class="app-navigation__label">{{ item.label }}</span>
          </RouterLink>
          <span
            v-else
            class="app-navigation__future"
            aria-disabled="true"
            tabindex="0"
            :aria-label="collapsed ? `${item.label}，${item.futureReason}` : undefined"
            :title="collapsed ? `${item.label}：${item.futureReason}` : item.futureReason"
          >
            <span aria-hidden="true">{{ item.symbol }}</span>
            <span class="app-navigation__label">{{ item.label }}</span>
            <span class="app-navigation__future-reason">
              {{ item.futureReason }}
            </span>
          </span>
        </li>
      </ul>
    </nav>

    <nav class="app-navigation__settings" aria-label="设置导航">
      <RouterLink
        :to="settingsNavigationItem.path!"
        :aria-label="collapsed ? settingsNavigationItem.label : undefined"
        :title="collapsed ? settingsNavigationItem.label : undefined"
      >
        <span aria-hidden="true">{{ settingsNavigationItem.symbol }}</span>
        <span class="app-navigation__label">{{ settingsNavigationItem.label }}</span>
      </RouterLink>
    </nav>
  </aside>
</template>
